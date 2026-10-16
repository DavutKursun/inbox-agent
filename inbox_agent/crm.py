"""A tiny CRM in SQLite: customers and bookings. Fixtures from scripts/make_fixtures.py."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import CRM_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, phone TEXT,
  last_visit TEXT, balance_due REAL DEFAULT 0, notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bookings (
  id TEXT PRIMARY KEY, customer_id INTEGER NOT NULL, start TEXT NOT NULL, duration_min INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'confirmed', reason TEXT DEFAULT '',
  FOREIGN KEY(customer_id) REFERENCES customers(id)
);
"""


class CRM:
    def __init__(self, path: Path | str = CRM_DB) -> None:
        self.path = str(path)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add_customer(self, name: str, email: str, phone: str = "", last_visit: str = "", balance_due: float = 0.0, notes: str = "") -> int:
        with self._conn() as c:
            cur = c.execute("INSERT INTO customers(name,email,phone,last_visit,balance_due,notes) VALUES (?,?,?,?,?,?)",
                            (name, email.lower(), phone, last_visit, balance_due, notes))
            return int(cur.lastrowid)

    def lookup(self, email: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM customers WHERE email = ?", (email.lower(),)).fetchone()
            if row is None:
                return None
            d = dict(row)
            d["bookings"] = [dict(b) for b in c.execute(
                "SELECT id, start, duration_min, status, reason FROM bookings WHERE customer_id = ? ORDER BY start", (d["id"],))]
            return d

    def get(self, customer_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
            return dict(row) if row else None

    def add_booking(self, booking_id: str, customer_id: int, start: str, duration_min: int, reason: str = "") -> dict:
        with self._conn() as c:
            c.execute("INSERT INTO bookings(id,customer_id,start,duration_min,reason) VALUES (?,?,?,?,?)",
                      (booking_id, customer_id, start, duration_min, reason))
        return {"booking_id": booking_id, "customer_id": customer_id, "start": start, "duration_min": duration_min, "status": "confirmed"}

    def cancel_booking(self, booking_id: str, reason: str = "") -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
            if row is None:
                return None
            c.execute("UPDATE bookings SET status='cancelled', reason=? WHERE id=?", (reason, booking_id))
            d = dict(row)
            d["status"] = "cancelled"
            return d

    def count(self) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM customers").fetchone()[0])
