"""Google Calendar behind the Calendar protocol (Milestone 5, live mode: CALENDAR_MODE=google).

The app works in naive local times (TIMEZONE, Europe/Istanbul). This adapter converts them to RFC 3339 for the
API and back, and applies the same slot rules as the fake calendar (opening hours, 30-minute grid, 14-day range,
today from 00:00). Busy time is every event that is neither cancelled nor marked "free" (transparent); all-day
events block the whole day. Bookings are inserted only when a human approves the queued action.

Live mode is a manual step; tests use a mocked client and never touch the network:
  1. Google Cloud console: enable the Google Calendar API, create an OAuth client of type "Desktop app" and
     save its JSON as .secrets/gcal_client.json (gitignored).
  2. python -m integrations.gcal auth      opens a browser once and writes .secrets/gcal_token.json
  3. python -m integrations.gcal slots     prints free slots on GCAL_CALENDAR_ID for the next 7 days
  4. CALENDAR_MODE=google GCAL_CALENDAR_ID=<calendar id> uvicorn app:app --port 7860
Use a dedicated test calendar: the seeded CRM bookings do not exist there, and approved bookings become real events.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inbox_agent.calendar import Busy, check_range, fits_opening, free_slots, overlaps
from inbox_agent.config import TIMEZONE

SECRETS = ROOT / ".secrets"
CLIENT_FILE = SECRETS / "gcal_client.json"
TOKEN_FILE = SECRETS / "gcal_token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]  # list, insert and delete events; nothing else


class GoogleCalendar:
    def __init__(self, service, calendar_id: str = "primary", today: dt.date | None = None, tz: str = TIMEZONE) -> None:
        """``service`` is a Calendar API client (``build_service()``, or a mock in tests)."""
        self.service, self.calendar_id = service, calendar_id
        self.today = today or dt.date.today()
        self.tz = ZoneInfo(tz)

    # --- conversions ---------------------------------------------------------------------------
    def _rfc3339(self, local: dt.datetime) -> str:
        return local.replace(tzinfo=self.tz).isoformat()

    def _local(self, when: dict) -> dt.datetime:
        if "dateTime" in when:
            return dt.datetime.fromisoformat(when["dateTime"]).astimezone(self.tz).replace(tzinfo=None)  # 3.11 parses "Z"
        return dt.datetime.combine(dt.date.fromisoformat(when["date"]), dt.time(0, 0))  # all-day event

    def _list(self, start: dt.datetime, end: dt.datetime) -> list[dict]:
        """Busy events overlapping [start, end), every page; cancelled and transparent events are skipped."""
        out, token = [], None
        while True:
            kwargs = {"calendarId": self.calendar_id, "timeMin": self._rfc3339(start), "timeMax": self._rfc3339(end),
                      "singleEvents": True, "orderBy": "startTime", "maxResults": 250}
            if token:
                kwargs["pageToken"] = token
            resp = self.service.events().list(**kwargs).execute()
            for ev in resp.get("items", []):
                if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
                    continue
                out.append({"id": ev.get("id", ""), "start": self._local(ev["start"]), "end": self._local(ev["end"]), "title": ev.get("summary", "")})
            token = resp.get("nextPageToken")
            if not token:
                return out

    def _busy(self, start: dt.datetime, end: dt.datetime) -> list[Busy]:
        return [(e["start"], e["end"], e["id"]) for e in self._list(start, end)]

    @staticmethod
    def _day_bounds(date_from: dt.date, date_to: dt.date) -> tuple[dt.datetime, dt.datetime]:
        return dt.datetime.combine(date_from, dt.time(0, 0)), dt.datetime.combine(date_to + dt.timedelta(days=1), dt.time(0, 0))

    # --- the Calendar protocol --------------------------------------------------------------------
    def find_slots(self, date_from: dt.date, date_to: dt.date, duration_min: int = 30, max_results: int = 12) -> list[dict]:
        check_range(date_from, date_to)  # before any API call
        first = max(date_from, self.today)
        if first > date_to:
            return []
        return free_slots(date_from, date_to, duration_min, max_results, self.today, self._busy(*self._day_bounds(first, date_to)))

    def is_free(self, start: dt.datetime, duration_min: int) -> bool:
        if not fits_opening(start, duration_min):
            return False
        end = start + dt.timedelta(minutes=duration_min)
        return not any(overlaps(start, end, b0, b1) for b0, b1, _ in self._busy(start, end))

    def book(self, start: dt.datetime, duration_min: int, title: str) -> dict:
        if not self.is_free(start, duration_min):
            raise ValueError("slot is not free")
        end = start + dt.timedelta(minutes=duration_min)
        body = {"summary": title, "description": "Booked by inbox-agent after a human approved it.",
                "start": {"dateTime": self._rfc3339(start), "timeZone": self.tz.key},
                "end": {"dateTime": self._rfc3339(end), "timeZone": self.tz.key}}
        ev = self.service.events().insert(calendarId=self.calendar_id, body=body).execute()
        return {"id": ev["id"], "start": start.isoformat(timespec="minutes"), "duration_min": duration_min, "title": title}

    def cancel(self, event_id: str) -> bool:
        from googleapiclient.errors import HttpError

        try:
            self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as exc:
            if (getattr(exc, "status_code", None) or exc.resp.status) in (404, 410):  # unknown or already deleted
                return False
            raise
        return True

    def events(self, date_from: dt.date, date_to: dt.date) -> list[dict]:
        return [{"id": e["id"], "start": e["start"].isoformat(timespec="minutes"), "duration_min": int((e["end"] - e["start"]).total_seconds() // 60),
                 "title": e["title"]} for e in self._list(*self._day_bounds(date_from, date_to))]


# --- live mode: credentials (manual) --------------------------------------------------------------------
def build_service(token_file: Path | str = TOKEN_FILE):
    """An authorized Calendar API client from the token that ``python -m integrations.gcal auth`` wrote; an expired
    token is refreshed and saved. Fails with instructions when there is no usable token, without opening a browser."""
    from google.oauth2.credentials import Credentials

    token_file = Path(token_file)
    if not token_file.exists():
        raise RuntimeError(f"no Google token at {token_file}: run `python -m integrations.gcal auth` once (manual step, opens a browser)")
    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            raise RuntimeError(f"the Google token at {token_file} is not usable: run `python -m integrations.gcal auth` again")
        from google.auth.transport.requests import Request

        creds.refresh(Request())
        token_file.write_text(creds.to_json(), encoding="utf-8")
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def authorize(client_file: Path | str = CLIENT_FILE, token_file: Path | str = TOKEN_FILE) -> Path:
    """The OAuth desktop flow: opens a browser, stores the token (mode 600). Run by hand, never in tests or CI."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_file, token_file = Path(client_file), Path(token_file)
    if not client_file.exists():
        raise SystemExit(f"save the OAuth client JSON (type Desktop app) as {client_file} first")
    creds = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES).run_local_server(port=0)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Google Calendar live mode (manual steps).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="run the OAuth desktop flow once and store the token in .secrets/")
    slots = sub.add_parser("slots", help="print free slots on GCAL_CALENDAR_ID")
    slots.add_argument("--days", type=int, default=7)
    args = ap.parse_args(argv)
    if args.cmd == "auth":
        print("token written to", authorize())
        return 0
    from inbox_agent.config import settings

    cal = GoogleCalendar(build_service(), calendar_id=settings.gcal_calendar_id)
    for s in cal.find_slots(cal.today, cal.today + dt.timedelta(days=args.days)):
        print(s["start"], f"{s['duration_min']} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
