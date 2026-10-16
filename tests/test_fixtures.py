from __future__ import annotations

import datetime as dt
import json
import sqlite3

from inbox_agent.config import fixtures_today
from inbox_agent.crm import CRM
from scripts.make_fixtures import SAMPLE_EMAILS, build

TODAY = dt.date(2026, 10, 19)  # a Monday


def _build(root):
    return build(today=TODAY, n=12, crm_path=root / "crm.sqlite", calendar_path=root / "calendar.json", samples_dir=root / "samples",
                 today_file=root / "fixtures_today.txt")


def test_fixtures_are_dated_and_cancel_sample_is_in_the_future(tmp_path):
    info = _build(tmp_path)
    assert (tmp_path / "fixtures_today.txt").read_text(encoding="utf-8").strip() == "2026-10-19"
    assert info["customers"] == CRM(tmp_path / "crm.sqlite").count() == 12
    busy = json.loads((tmp_path / "calendar.json").read_text(encoding="utf-8"))["busy"]
    assert busy and min(b["start"] for b in busy) >= "2026-10-19" and max(b["start"] for b in busy) < "2026-11-03"
    assert sorted(p.name for p in (tmp_path / "samples").glob("*.json")) == sorted(f"{s['name']}.json" for s in SAMPLE_EMAILS)
    cancel = json.loads((tmp_path / "samples" / "cancel.json").read_text(encoding="utf-8"))
    rec = CRM(tmp_path / "crm.sqlite").lookup(cancel["from"])
    referenced = [b for b in rec["bookings"] if b["start"].replace("T", " at ") in cancel["body"]]
    assert len(referenced) == 1 and referenced[0]["status"] == "confirmed" and referenced[0]["start"] == info["cancel_booking_start"]
    assert referenced[0]["start"][:10] > "2026-10-19" and referenced[0]["start"] >= "2026-10-21"  # outside the 24-hour fee window


def test_fixtures_are_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert _build(a) == _build(b)
    for name in ("calendar.json", "fixtures_today.txt", *(f"samples/{s['name']}.json" for s in SAMPLE_EMAILS)):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    for table in ("customers", "bookings"):
        rows = [sqlite3.connect(p / "crm.sqlite").execute(f"SELECT * FROM {table} ORDER BY id").fetchall() for p in (a, b)]
        assert rows[0] == rows[1] and rows[0], table


def test_fixtures_today_reads_file_or_falls_back(tmp_path):
    f = tmp_path / "fixtures_today.txt"
    assert fixtures_today(f) == dt.date.today()  # no file yet
    f.write_text("2026-10-19\n", encoding="utf-8")
    assert fixtures_today(f) == dt.date(2026, 10, 19)
    f.write_text("not a date", encoding="utf-8")
    assert fixtures_today(f) == dt.date.today()
