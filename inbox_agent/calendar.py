"""Calendar interface + a fake backed by data/calendar.json (seeded busy blocks).

Opening hours come from the KB page "hours" but are hard-coded here too so the fake is
self-contained: Mon–Fri 09:00–18:00, Sat 10:00–14:00, Sunday closed. Slots are on a 30-minute grid.
`integrations/gcal.py` (Milestone 5) implements the same interface against Google Calendar.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol

from .config import CALENDAR_JSON

OPENING = {0: (9, 18), 1: (9, 18), 2: (9, 18), 3: (9, 18), 4: (9, 18), 5: (10, 14)}  # weekday → (open, close); 6 = Sunday closed
GRID_MIN = 30


class Calendar(Protocol):
    def find_slots(self, date_from: date, date_to: date, duration_min: int) -> list[dict]: ...
    def book(self, start: datetime, duration_min: int, title: str) -> dict: ...
    def cancel(self, event_id: str) -> bool: ...


def _overlaps(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> bool:
    return a0 < b1 and b0 < a1


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

    def _busy_ranges(self) -> list[tuple[datetime, datetime, str]]:
        out = []
        for b in self.busy:
            s = datetime.fromisoformat(b["start"])
            out.append((s, s + timedelta(minutes=int(b["duration_min"])), b.get("id", "")))
        return out

    def find_slots(self, date_from: date, date_to: date, duration_min: int = 30, max_results: int = 12) -> list[dict]:
        if date_to < date_from:
            raise ValueError("date_to before date_from")
        if (date_to - date_from).days > 14:
            raise ValueError("range longer than 14 days")
        if date_from < self.today:
            date_from = self.today
        busy = self._busy_ranges()
        slots: list[dict] = []
        d = date_from
        while d <= date_to and len(slots) < max_results:
            hours = OPENING.get(d.weekday())
            if hours:
                open_h, close_h = hours
                t = datetime.combine(d, time(open_h, 0))
                end_of_day = datetime.combine(d, time(close_h, 0))
                while t + timedelta(minutes=duration_min) <= end_of_day and len(slots) < max_results:
                    t1 = t + timedelta(minutes=duration_min)
                    if t > datetime.combine(self.today, datetime.now().time()) or d > self.today:
                        if not any(_overlaps(t, t1, b0, b1) for b0, b1, _ in busy):
                            slots.append({"start": t.isoformat(timespec="minutes"), "duration_min": duration_min})
                    t += timedelta(minutes=GRID_MIN)
            d += timedelta(days=1)
        return slots

    def is_free(self, start: datetime, duration_min: int) -> bool:
        hours = OPENING.get(start.weekday())
        if not hours:
            return False
        end = start + timedelta(minutes=duration_min)
        if start.time() < time(hours[0], 0) or end.time() > time(hours[1], 0) or end.date() != start.date():
            return False
        return not any(_overlaps(start, end, b0, b1) for b0, b1, _ in self._busy_ranges())

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
