from __future__ import annotations

import datetime as dt
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import gradio as gr
import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app
from inbox_agent.calendar import FakeCalendar
from inbox_agent.costs import BudgetGuard
from inbox_agent.crm import CRM
from inbox_agent.fake_provider import FakeProvider
from inbox_agent.ui import PASTE, QUEUE_HEADERS, ReviewUI
from scripts.make_fixtures import build

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 10, 19)


@pytest.fixture
def ui(tmp_path):
    build(today=TODAY, n=20, crm_path=tmp_path / "crm.sqlite", calendar_path=tmp_path / "calendar.json", samples_dir=tmp_path / "samples", today_file=tmp_path / "t.txt")
    app = create_app(provider=FakeProvider(), crm_path=tmp_path / "crm.sqlite", calendar_path=tmp_path / "calendar.json", store_root=tmp_path / "runtime",
                     budget=BudgetGuard(tmp_path / "budget.json", 1.0, 100), today=TODAY, samples_dir=tmp_path / "samples")
    return app, app.state.ui, tmp_path, CRM(tmp_path / "crm.sqlite").list_customers()


def test_ui_builds_and_is_mounted(ui):
    app, review, _tmp, _customers = ui
    assert isinstance(review, ReviewUI) and isinstance(review.build(), gr.Blocks)
    client = TestClient(app)
    r = client.get("/ui", follow_redirects=True)
    assert r.status_code == 200 and "gradio" in r.text.lower()
    names = list(review.samples())
    assert names == ["booking", "cancel", "question", "billing", "complaint", "injection"] and review.pick_sample(PASTE) == ("", "", "")
    assert review.pick_sample("booking")[1] == "Check-up appointment"
    assert "fake-model" in review.header() and "daily cap 100 calls" in review.header()


def test_handle_and_approve_through_the_ui_changes_the_calendar(ui):
    _app, review, tmp, customers = ui
    sender = customers[0]["email"]
    r = review.handle(sender, "Check-up", "Could I book a check-up this week?")
    assert not r.error and r.result.intent == "booking" and len(r.result.actions_pending) == 1
    assert "**Intent:** booking" in r.summary_md and "Pending approval: book_slot" in r.summary_md.replace("**", "")
    assert r.draft_body.startswith("We have requested") and "no citations" in r.citations_md and "unverified" not in r.citations_md
    assert [row[1] for row in r.trace_rows] == ["lookup_customer", "find_slots", "book_slot", "draft_reply", "finish"] and r.trace_rows[2][3] == "queued"
    assert "free tier" in r.stats_md and "turns" in r.stats_md
    action = r.result.actions_pending[0]
    rows = review.pending_rows()
    assert len(rows) == 1 and rows[0][0] == action.action_id and rows[0][1] == "book_slot" and rows[0][3] == sender and len(rows[0]) == len(QUEUE_HEADERS)
    start = action.args["start"]
    assert not any(row[1] == start and "check-up" in row[3] for row in review.calendar_rows())  # not in the calendar yet
    msg = review.resolve(action.action_id, True)
    assert "executed" in msg and review.pending_rows() == []
    assert not FakeCalendar(tmp / "calendar.json", today=TODAY).is_free(dt.datetime.fromisoformat(start), 30)
    assert any(row[1] == start and "check-up" in row[3] for row in review.calendar_rows())  # now it is
    assert review.recent()[0][:3] == [r.result.thread_id, "booking", "no"] and review.recent()[0][3] == 1
    assert "Unknown action" in review.resolve("act_nope", True)


def test_reject_through_the_ui_leaves_the_calendar(ui):
    _app, review, tmp, customers = ui
    r = review.handle(customers[1]["email"], "Appointment", "I would like an appointment next week please.")
    action = r.result.actions_pending[0]
    msg = review.resolve(action.action_id, False)
    assert "rejected" in msg and review.pending_rows() == []
    assert FakeCalendar(tmp / "calendar.json", today=TODAY).is_free(dt.datetime.fromisoformat(action.args["start"]), 30)


def test_ui_refuses_bad_input_without_raising(ui):
    _app, review, _tmp, customers = ui
    assert review.handle("not-an-email", "x", "hello there").error.startswith("Invalid email")
    assert "2000" in review.handle(customers[2]["email"], "x", "a" * 2001).error
    review.budget.max_daily_calls = 0
    assert "budget" in review.handle(customers[2]["email"], "Hours", "When are you open?").error


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_headless_smoke_health_and_ui():
    port = _free_port()
    env = {**os.environ, "INBOX_FAKE_PROVIDER": "1", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", str(port), "--host", "127.0.0.1", "--log-level", "warning"],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 10
        health = None
        while time.time() < deadline:
            try:
                health = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
                break
            except httpx.HTTPError:
                time.sleep(0.25)
        assert health is not None and health.status_code == 200, "server did not come up within 10 s"
        assert health.json()["provider"] == "fake"
        page = httpx.get(f"http://127.0.0.1:{port}/ui", follow_redirects=True, timeout=5.0)
        assert page.status_code == 200 and "gradio" in page.text.lower()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
