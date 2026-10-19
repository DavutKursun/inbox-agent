"""Tool registry: name → (args schema, description, implementation). The model sees the schemas;
the loop validates arguments with Pydantic before anything runs, and consults policy.py.

Implementations take validated args and a ``Context`` (crm, calendar, kb, store, thread) and return
JSON-serialisable results. ``book_slot``/``cancel_booking`` are approval-level: the loop queues them
and calls ``execute`` only after a human approves.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Callable

from pydantic import BaseModel

from .calendar import GRID_MIN, Calendar, weekday_name
from .config import VISIT_MINUTES
from .crm import CRM
from .kb import KnowledgeBase
from .schema import (BookSlotArgs, CancelBookingArgs, Draft, DraftReplyArgs, EscalateArgs, FindSlotsArgs, FinishArgs,
                     LookupCustomerArgs, SearchKBArgs)
from .store import Store


@dataclass
class Context:
    crm: CRM
    calendar: Calendar
    kb: KnowledgeBase
    store: Store
    thread_id: str
    customer_email: str
    today: date = field(default_factory=date.today)  # the agent's "now" for validity checks; Agent.handle sets it
    retrieved: set[str] = field(default_factory=set)  # chunk_ids search_kb returned in this thread: the only citable ones
    calls: dict[str, int] = field(default_factory=dict)  # calls per tool in this thread, for policy.CALL_LIMITS
    draft: Draft | None = None
    escalation: str = ""
    finish: FinishArgs | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    run: Callable[[BaseModel, Context], Any]
    check: Callable[[BaseModel, Context], str | None] | None = None  # approval-level only: error string → not queued
    preview: Callable[[BaseModel, Context], dict] | None = None  # approval-level only: facts merged into the "queued" result


# --- implementations --------------------------------------------------------------
def _hours_from_today(today: date, start_iso: str) -> float:
    """Hours from the agent's today at 00:00 to a start time: the model reads this instead of doing date arithmetic."""
    return round((datetime.fromisoformat(start_iso) - datetime.combine(today, time(0, 0))).total_seconds() / 3600, 1)


def _with_dates(ctx: Context, item: dict, key: str = "start") -> dict:
    """Add "weekday" and "starts_in_hours" to a booking / slot / queued action, computed from its ``key`` datetime."""
    return {**item, "weekday": weekday_name(datetime.fromisoformat(item[key])), "starts_in_hours": _hours_from_today(ctx.today, item[key])}


def _lookup_customer(a: LookupCustomerArgs, ctx: Context):
    if a.email.lower() != ctx.customer_email.lower():  # the sender's own record only: no browsing other customers
        return {"error": "can only look up the sender"}
    rec = ctx.crm.lookup(a.email)
    if rec is None:
        return {"found": False}
    rec = dict(rec)
    rec["found"] = True
    rec["bookings"] = [_with_dates(ctx, b) for b in rec["bookings"]]
    return rec


def _search_kb(a: SearchKBArgs, ctx: Context):
    results = ctx.kb.search(a.query, k=3)
    ctx.retrieved.update(r["chunk_id"] for r in results)
    return {"results": results}


def _find_slots(a: FindSlotsArgs, ctx: Context):
    d0, d1 = date.fromisoformat(a.date_from), date.fromisoformat(a.date_to)
    return {"slots": [_with_dates(ctx, s) for s in ctx.calendar.find_slots(d0, d1, a.duration_min)]}


def _book_slot(a: BookSlotArgs, ctx: Context):
    cust = ctx.crm.get(a.customer_id)
    if cust is None:
        return {"error": "unknown customer_id"}
    if cust["email"].lower() != ctx.customer_email.lower():
        return {"error": "customer_id does not belong to the sender of this email"}
    ev = ctx.calendar.book(datetime.fromisoformat(a.start), a.duration_min, f"{cust['name']} — {a.reason or 'visit'}")
    return ctx.crm.add_booking(ev["id"], a.customer_id, ev["start"], a.duration_min, a.reason)


def _cancel_booking(a: CancelBookingArgs, ctx: Context):
    rec = ctx.crm.lookup(ctx.customer_email)
    if rec is None or a.booking_id not in {b["id"] for b in rec["bookings"]}:
        return {"error": "booking_id does not belong to the sender of this email"}
    ctx.calendar.cancel(a.booking_id)
    return ctx.crm.cancel_booking(a.booking_id, a.reason) or {"error": "unknown booking"}


# --- queue-time checks for approval-level tools: an error string means the call is answered, never queued ---
def _check_book_slot(a: BookSlotArgs, ctx: Context) -> str | None:
    cust = ctx.crm.get(a.customer_id)
    if cust is None:
        return "unknown customer_id"
    if cust["email"].lower() != ctx.customer_email.lower():
        return "customer_id does not belong to the sender of this email"
    hint = "use a slot returned by find_slots"
    start = datetime.fromisoformat(a.start)  # format and "no timezone offset" are validated by BookSlotArgs
    if start.date() < ctx.today:
        return f"start is in the past (today is {ctx.today.isoformat()})"
    if start.minute % GRID_MIN or start.second or start.microsecond:
        return f"start must be on the {GRID_MIN}-minute grid ({hint})"
    if not ctx.calendar.is_free(start, a.duration_min):
        return f"slot is not free or outside opening hours ({hint})"
    return None


def _check_cancel_booking(a: CancelBookingArgs, ctx: Context) -> str | None:
    rec = ctx.crm.lookup(ctx.customer_email)
    booking = next((b for b in (rec["bookings"] if rec else []) if b["id"] == a.booking_id), None)
    if booking is None:
        return "booking_id does not belong to the sender of this email"
    if booking["status"] != "confirmed":
        return f"booking is already {booking['status']}"
    return None


# --- queued-result previews: facts the model needs right after queuing (dates it must not compute itself) ---------
def _preview_book_slot(a: BookSlotArgs, ctx: Context) -> dict:
    return _with_dates(ctx, {"start": a.start})


def _preview_cancel_booking(a: CancelBookingArgs, ctx: Context) -> dict:
    rec = ctx.crm.lookup(ctx.customer_email) or {"bookings": []}
    booking = next(b for b in rec["bookings"] if b["id"] == a.booking_id)  # exists: the check ran first
    return _with_dates(ctx, {"booking_start": booking["start"]}, key="booking_start")


# A draft "states a price or policy" when it contains an amount (₺, €, TL, EUR, a number followed by TL/₺) or one of these
# policy phrases. Bare words such as "refund", "cancellation" or "price" are mentions, not claims. Documented in docs/DESIGN.md.
_CLAIM_WORDS = re.compile(r"₺|€|\bTL\b|\bEUR\b|\d[\d.,]*\s?(?:TL|₺)|\bfree of charge\b|\bcharged\b|\bfee of\b|\bwithin 24 hours\b|\bopen on\b|\bclosed on\b", re.I)


def _draft_reply(a: DraftReplyArgs, ctx: Context):
    cites = [c for c in a.citations if c in ctx.retrieved]  # only chunks search_kb returned in this thread count
    ignored = [c for c in a.citations if c not in ctx.retrieved]
    unverified = bool(_CLAIM_WORDS.search(a.body)) and not cites
    ctx.draft = Draft(subject=a.subject, body=a.body, citations=cites, unverified_claim=unverified)
    return {"saved": True, "unverified_claim": unverified, "ignored_citations": ignored}


def _escalate(a: EscalateArgs, ctx: Context):
    ctx.escalation = a.reason
    return {"escalated": True}


def _finish(a: FinishArgs, ctx: Context):
    ctx.finish = a
    return {"done": True}


TOOLS: dict[str, Tool] = {
    "lookup_customer": Tool("lookup_customer", "Find the sender's customer record (and bookings). Only the sender's own address can be looked up.", LookupCustomerArgs, _lookup_customer),
    "search_kb": Tool("search_kb", "Search the clinic knowledge base (hours, prices, policies). Cite the returned chunk_id in drafts.", SearchKBArgs, _search_kb),
    "find_slots": Tool("find_slots", f"List free appointment slots between two dates (default {VISIT_MINUTES} min).", FindSlotsArgs, _find_slots),
    "book_slot": Tool("book_slot", "Book a slot returned by find_slots for the sender. Queued for human approval.", BookSlotArgs, _book_slot, _check_book_slot,
                      _preview_book_slot),
    "cancel_booking": Tool("cancel_booking", "Cancel one of the sender's confirmed bookings. Queued for human approval.", CancelBookingArgs, _cancel_booking,
                           _check_cancel_booking, _preview_cancel_booking),
    "draft_reply": Tool("draft_reply", "Save the reply draft (not sent). Cite chunk_ids returned by search_kb in this thread for any price/policy statement.", DraftReplyArgs, _draft_reply),
    "escalate": Tool("escalate", "Hand the thread to a human: complaints, legal, refunds, anything unclear or suspicious.", EscalateArgs, _escalate),
    "finish": Tool("finish", "End the thread with the detected intent and a one-line summary.", FinishArgs, _finish),
}


def tool_specs() -> list[dict]:
    """OpenAI-style function specs; providers.py converts for Anthropic."""
    out = []
    for t in TOOLS.values():
        schema = t.args_model.model_json_schema()
        schema.pop("title", None)
        out.append({"type": "function", "function": {"name": t.name, "description": t.description, "parameters": schema}})
    return out


def new_thread_id() -> str:
    return f"thr_{uuid.uuid4().hex[:10]}"
