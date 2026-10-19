"""Google Calendar adapter with a mocked API client: no network, no OAuth."""

from __future__ import annotations

import datetime as dt

import httplib2
import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from app import create_app
from inbox_agent.agent import Agent
from inbox_agent.config import Settings
from inbox_agent.costs import BudgetGuard
from inbox_agent.crm import CRM
from inbox_agent.fake_provider import FakeProvider
from inbox_agent.kb import KnowledgeBase
from inbox_agent.schema import Email
from inbox_agent.store import Store
from inbox_agent.tools import Context
from integrations import gcal
from integrations.gcal import GoogleCalendar
from scripts.make_fixtures import build
from tests.conftest import ScriptedProvider

TODAY = dt.date(2026, 10, 19)  # a Monday
TUE, WED, SUN = dt.date(2026, 10, 20), dt.date(2026, 10, 21), dt.date(2026, 10, 25)


class _Request:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeService:
    """Mimics googleapiclient's ``service.events().list(...).execute()`` chain, with paging and real HttpError."""

    def __init__(self, items: list[dict], page_size: int = 2) -> None:
        self.items, self.page_size, self.calls = items, page_size, []

    def events(self):
        return self

    @staticmethod
    def _bounds(ev: dict) -> tuple[dt.datetime, dt.datetime]:
        def one(w):
            if "dateTime" in w:
                return dt.datetime.fromisoformat(w["dateTime"])
            return dt.datetime.combine(dt.date.fromisoformat(w["date"]), dt.time(0), tzinfo=dt.timezone(dt.timedelta(hours=3)))
        return one(ev["start"]), one(ev["end"])

    def list(self, **kw):
        self.calls.append(("list", kw))

        def run():
            lo, hi = dt.datetime.fromisoformat(kw["timeMin"]), dt.datetime.fromisoformat(kw["timeMax"])
            match = [ev for ev in self.items if self._bounds(ev)[0] < hi and lo < self._bounds(ev)[1]]
            i = int(kw.get("pageToken") or 0)
            page = {"items": match[i:i + self.page_size]}
            if i + self.page_size < len(match):
                page["nextPageToken"] = str(i + self.page_size)
            return page
        return _Request(run)

    def insert(self, calendarId, body):
        self.calls.append(("insert", {"calendarId": calendarId, "body": body}))

        def run():
            ev = {"id": f"g{len(self.items) + 1:03d}", "status": "confirmed", **body}
            self.items.append(ev)
            return ev
        return _Request(run)

    def delete(self, calendarId, eventId):
        self.calls.append(("delete", {"calendarId": calendarId, "eventId": eventId}))

        def run():
            if eventId == "boom":
                raise HttpError(httplib2.Response({"status": 500}), b"backend error")
            for ev in self.items:
                if ev["id"] == eventId:
                    self.items.remove(ev)
                    return ""
            raise HttpError(httplib2.Response({"status": 404}), b"not found")
        return _Request(run)

    def kinds(self) -> list[str]:
        return [k for k, _ in self.calls]


def _items() -> list[dict]:
    return [
        {"id": "a1", "summary": "Busy 09-10", "start": {"dateTime": "2026-10-20T09:00:00+03:00"}, "end": {"dateTime": "2026-10-20T10:00:00+03:00"}},
        {"id": "a2", "summary": "UTC event", "start": {"dateTime": "2026-10-20T08:00:00Z"}, "end": {"dateTime": "2026-10-20T08:30:00Z"}},  # 11:00 local
        {"id": "a3", "summary": "Marked free", "transparency": "transparent", "start": {"dateTime": "2026-10-20T12:00:00+03:00"}, "end": {"dateTime": "2026-10-20T12:30:00+03:00"}},
        {"id": "a4", "summary": "Cancelled", "status": "cancelled", "start": {"dateTime": "2026-10-20T13:00:00+03:00"}, "end": {"dateTime": "2026-10-20T13:30:00+03:00"}},
        {"id": "a5", "summary": "Closed for training", "start": {"date": "2026-10-21"}, "end": {"date": "2026-10-22"}},
    ]


@pytest.fixture
def cal():
    svc = FakeService(_items())
    return GoogleCalendar(svc, calendar_id="clinic-test", today=TODAY), svc


def test_find_slots_reads_busy_time_from_the_api(cal):
    gc, svc = cal
    starts = [s["start"] for s in gc.find_slots(TUE, TUE, 30, max_results=40)]
    assert starts[0] == "2026-10-20T10:00" and "2026-10-20T09:00" not in starts and "2026-10-20T09:30" not in starts
    assert "2026-10-20T11:00" not in starts  # the UTC event, converted to Istanbul time
    assert "2026-10-20T12:00" in starts and "2026-10-20T13:00" in starts  # transparent and cancelled events do not block
    assert all(s < "2026-10-20T18:00" for s in starts)
    assert svc.kinds().count("list") >= 2 and svc.calls[0][1]["calendarId"] == "clinic-test"  # every page was read
    assert svc.calls[0][1]["timeMin"] == "2026-10-20T00:00:00+03:00" and svc.calls[0][1]["singleEvents"] is True
    assert gc.find_slots(WED, WED) == []  # the all-day event blocks the day
    assert gc.find_slots(SUN, SUN) == []  # closed
    before = len(svc.calls)
    assert gc.find_slots(dt.date(2026, 10, 1), dt.date(2026, 10, 2)) == [] and len(svc.calls) == before  # past range: no API call
    with pytest.raises(ValueError):
        gc.find_slots(TUE, TODAY)
    with pytest.raises(ValueError):
        gc.find_slots(TODAY, dt.date(2026, 11, 19))
    assert len(svc.calls) == before  # invalid ranges never reach the API


def test_is_free_book_and_cancel(cal):
    gc, svc = cal
    assert not gc.is_free(dt.datetime(2026, 10, 20, 9, 30), 30) and gc.is_free(dt.datetime(2026, 10, 20, 10, 0), 30)
    assert not gc.is_free(dt.datetime(2026, 10, 20, 17, 45), 30) and not gc.is_free(dt.datetime(2026, 10, 25, 10, 0), 30)
    ev = gc.book(dt.datetime(2026, 10, 20, 10, 0), 30, "Ada Yılmaz — check-up")
    assert ev == {"id": "g006", "start": "2026-10-20T10:00", "duration_min": 30, "title": "Ada Yılmaz — check-up"}
    body = next(c for k, c in svc.calls if k == "insert")["body"]
    assert body["start"] == {"dateTime": "2026-10-20T10:00:00+03:00", "timeZone": "Europe/Istanbul"}
    assert body["end"] == {"dateTime": "2026-10-20T10:30:00+03:00", "timeZone": "Europe/Istanbul"} and body["summary"] == "Ada Yılmaz — check-up"
    assert not gc.is_free(dt.datetime(2026, 10, 20, 10, 0), 30)  # the new event is busy time now
    inserts = svc.kinds().count("insert")
    with pytest.raises(ValueError, match="not free"):
        gc.book(dt.datetime(2026, 10, 20, 9, 0), 30, "x")
    assert svc.kinds().count("insert") == inserts  # nothing inserted for a busy slot
    assert gc.cancel("g006") is True and gc.is_free(dt.datetime(2026, 10, 20, 10, 0), 30)
    assert gc.cancel("missing") is False  # 404: unknown or already deleted
    with pytest.raises(HttpError):
        gc.cancel("boom")  # other errors surface


def test_events_listing_for_the_ui(cal):
    gc, _ = cal
    rows = gc.events(TUE, WED)
    assert [(e["id"], e["start"], e["duration_min"]) for e in rows] == [
        ("a1", "2026-10-20T09:00", 60), ("a2", "2026-10-20T11:00", 30), ("a5", "2026-10-21T00:00", 1440)]


def test_build_service_without_a_token_explains_the_manual_step(tmp_path):
    with pytest.raises(RuntimeError, match=r"python -m integrations\.gcal auth"):
        gcal.build_service(tmp_path / "missing-token.json")


@pytest.fixture
def crm_world(tmp_path):
    crm = CRM(tmp_path / "crm.sqlite")
    ada = crm.add_customer("Ada Yılmaz", "ada@example.com", "0532", "2026-03-01", 0.0)
    store = Store(tmp_path / "runtime")
    return tmp_path, crm, ada, store


def test_agent_books_on_google_calendar_only_after_approval(crm_world):
    tmp, crm, ada, store = crm_world
    svc = FakeService(_items())
    kb = KnowledgeBase()

    def factory(thread_id, customer_email):
        return Context(crm=CRM(tmp / "crm.sqlite"), calendar=GoogleCalendar(svc, today=TODAY), kb=kb, store=store, thread_id=thread_id, customer_email=customer_email)

    p = ScriptedProvider([[("find_slots", {"date_from": "2026-10-20", "date_to": "2026-10-20"})],
                          [("book_slot", {"customer_id": ada, "start": "2026-10-20T10:00", "duration_min": 30, "reason": "check-up"})],
                          [("draft_reply", {"subject": "Re", "body": "Tuesday 10:00 is requested; reception will confirm it."}),
                           ("finish", {"intent": "booking", "summary": "Tue 10:00 queued for approval"})]])
    agent = Agent(p, factory, today=TODAY)
    res = agent.handle(Email(**{"from": "ada@example.com", "subject": "check-up", "body": "Tuesday morning?"}))
    assert res.actions_taken[0].result["slots"][0]["start"] == "2026-10-20T10:00" and res.actions_taken[1].status == "queued"
    assert "insert" not in svc.kinds()  # queued, not booked
    action = agent.resolve(res.actions_pending[0].action_id, approve=True)
    assert action.status == "executed" and svc.kinds().count("insert") == 1
    assert crm.lookup("ada@example.com")["bookings"][0]["id"] == action.result["booking_id"]


def test_app_uses_google_calendar_when_calendar_mode_is_google(tmp_path, monkeypatch):
    build(today=TODAY, n=20, crm_path=tmp_path / "crm.sqlite", calendar_path=tmp_path / "calendar.json", samples_dir=tmp_path / "samples", today_file=tmp_path / "t.txt")
    svc = FakeService(_items())
    monkeypatch.setenv("CALENDAR_MODE", "google")
    monkeypatch.setenv("GCAL_CALENDAR_ID", "clinic-test")
    monkeypatch.setattr(gcal, "build_service", lambda *a, **k: svc)
    app = create_app(settings=Settings(), provider=FakeProvider(), crm_path=tmp_path / "crm.sqlite", calendar_path=tmp_path / "calendar.json",
                     store_root=tmp_path / "runtime", budget=BudgetGuard(tmp_path / "b.json", 1.0, 100), today=TODAY, samples_dir=tmp_path / "samples")
    client = TestClient(app)
    sender = CRM(tmp_path / "crm.sqlite").list_customers()[0]["email"]
    res = client.post("/handle", json={"from": sender, "subject": "Check-up", "body": "Could I book a check-up this week?"}).json()
    action = res["actions_pending"][0]
    assert action["args"]["start"] == "2026-10-19T09:00" and "insert" not in svc.kinds()
    assert client.post(f"/approve/{action['action_id']}").json()["status"] == "executed"
    assert svc.kinds().count("insert") == 1 and all(c["calendarId"] == "clinic-test" for k, c in svc.calls if k in ("list", "insert"))
    assert any(row[1] == "2026-10-19T09:00" for row in app.state.ui.calendar_rows())  # the review UI reads the same calendar
