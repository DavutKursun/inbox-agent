"""Calendar interface, the slot rules every calendar shares, and a fake backed by data/calendar.json.

Opening hours come from the KB page "hours" but are hard-coded here too so every calendar applies the same rules:
Mon–Fri 09:00–18:00, Sat 10:00–14:00, Sunday closed. Slots are on a 30-minute grid, a query spans at most 14 days.
`today` is injected (default: the real date) and is the only notion of "now": the whole of today counts as
available from 00:00 and the wall clock is never consulted, so runs with --today are reproducible.
`integrations/gcal.py` implements the same interface against Google Calendar (CALENDAR_MODE=google).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol

from .config import CALENDAR_JSON

OPENING = {0: (9, 18), 1: (9, 18), 2: (9, 18), 3: (9, 18), 4: (9, 18), 5: (10, 14)}  # weekday → (open, close); 6 = Sunday closed
GRID_MIN = 30
MAX_RANGE_DAYS = 14
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
Busy = tuple[datetime, datetime, str]  # start, end, event id (naive local times)


def weekday_name(d: date | datetime) -> str:
    """Locale-independent short weekday name ("Tue"), used in the prompt and in tool results."""
    return WEEKDAYS[d.weekday()]


class Calendar(Protocol):
    def find_slots(self, date_from: date, date_to: date, duration_min: int) -> list[dict]: ...
    def is_free(self, start: datetime, duration_min: int) -> bool: ...
    def book(self, start: datetime, duration_min: int, title: str) -> dict: ...
    def cancel(self, event_id: str) -> bool: ...
    def events(self, date_from: date, date_to: date) -> list[dict]: ...  # {id, start, duration_min, title} for the review UI


# --- the shared rules ------------------------------------------------------------------------
def overlaps(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> bool:
    return a0 < b1 and b0 < a1


def check_range(date_from: date, date_to: date) -> None:
    if date_to < date_from:
        raise ValueError("date_to before date_from")
    if (date_to - date_from).days > MAX_RANGE_DAYS:
        raise ValueError(f"range longer than {MAX_RANGE_DAYS} days")


def fits_opening(start: datetime, duration_min: int) -> bool:
    """The visit starts and ends inside one day's opening hours."""
    hours = OPENING.get(start.weekday())
    if not hours:
        return False
    end = start + timedelta(minutes=duration_min)
    return not (start.time() < time(hours[0], 0) or end.time() > time(hours[1], 0) or end.date() != start.date())


def free_slots(date_from: date, date_to: date, duration_min: int, max_results: int, today: date, busy: list[Busy]) -> list[dict]:
    """Grid slots inside opening hours that overlap no busy range; dates before ``today`` are skipped."""
    check_range(date_from, date_to)
    date_from = max(date_from, today)
    slots: list[dict] = []
    d = date_from
    while d <= date_to and len(slots) < max_results:
        hours = OPENING.get(d.weekday())
        if hours:
            t = datetime.combine(d, time(hours[0], 0))
            end_of_day = datetime.combine(d, time(hours[1], 0))
            while t + timedelta(minutes=duration_min) <= end_of_day and len(slots) < max_results:
                t1 = t + timedelta(minutes=duration_min)
                if not any(overlaps(t, t1, b0, b1) for b0, b1, _ in busy):
                    slots.append({"start": t.isoformat(timespec="minutes"), "duration_min": duration_min})
                t += timedelta(minutes=GRID_MIN)
        d += timedelta(days=1)
    return slots


def make_calendar_factory(settings, calendar_path: Path | str, today: date) -> Callable[[], Calendar]:
    """CALENDAR_MODE=google: one authorized Google client shared by the app (live mode, see integrations/gcal.py);
    anything else: the fake calendar file. Each call returns a calendar for one thread or one UI refresh."""
    if getattr(settings, "calendar_mode", "fake") == "google":
        from integrations.gcal import GoogleCalendar, build_service

        service = build_service()
        return lambda: GoogleCalendar(service, calendar_id=settings.gcal_calendar_id, today=today)
    return lambda: FakeCalendar(calendar_path, today=today)


# --- the fake --------------------------------------------------------------------------------------
class FakeCalendar:
    def __init__(self, path: Path | str = CALENDAR_JSON, today: date | None = None) -> None:
        self.path = Path(path)
        self.today = today or date.today()
        self.busy: list[dict] = []
        if self.path.exists():
            self.busy = json.loads(self.path.read_text(encoding="utf-8")).get("busy", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"busy": self.busy}, indent=1), encoding="utf-8")

    def _busy_ranges(self) -> list[Busy]:
        out = []
        for b in self.busy:
            s = datetime.fromisoformat(b["start"])
            out.append((s, s + timedelta(minutes=int(b["duration_min"])), b.get("id", "")))
        return out

    def find_slots(self, date_from: date, date_to: date, duration_min: int = 30, max_results: int = 12) -> list[dict]:
        return free_slots(date_from, date_to, duration_min, max_results, self.today, self._busy_ranges())

    def is_free(self, start: datetime, duration_min: int) -> bool:
        end = start + timedelta(minutes=duration_min)
        return fits_opening(start, duration_min) and not any(overlaps(start, end, b0, b1) for b0, b1, _ in self._busy_ranges())

    def book(self, start: datetime, duration_min: int, title: str) -> dict:
        if not self.is_free(start, duration_min):
            raise ValueError("slot is not free")
        ev = {"id": f"evt_{uuid.uuid4().hex[:8]}", "start": start.isoformat(timespec="minutes"), "duration_min": duration_min, "title": title}
        self.busy.append(ev)
        self.save()
        return ev

    def cancel(self, event_id: str) -> bool:
        before = len(self.busy)
        self.busy = [b for b in self.busy if b.get("id") != event_id]
        if len(self.busy) != before:
            self.save()
            return True
        return False

    def events(self, date_from: date, date_to: date) -> list[dict]:
        lo, hi = date_from.isoformat(), date_to.isoformat()
        rows = [{"id": b.get("id", ""), "start": b["start"], "duration_min": int(b["duration_min"]), "title": b.get("title", "")}
                for b in self.busy if lo <= b["start"][:10] <= hi]
        return sorted(rows, key=lambda e: e["start"])
