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
from datetime import date, datetime
from typing import Any, Callable

from pydantic import BaseModel

from .calendar import FakeCalendar
from .config import VISIT_MINUTES
from .crm import CRM
from .kb import KnowledgeBase
from .schema import (BookSlotArgs, CancelBookingArgs, Draft, DraftReplyArgs, EscalateArgs, FindSlotsArgs, FinishArgs,
                     LookupCustomerArgs, SearchKBArgs)
from .store import Store


@dataclass
class Context:
    crm: CRM
    calendar: FakeCalendar
    kb: KnowledgeBase
    store: Store
    thread_id: str
    customer_email: str
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


# --- implementations --------------------------------------------------------------
def _lookup_customer(a: LookupCustomerArgs, ctx: Context):
    rec = ctx.crm.lookup(a.email)
    if rec is None:
        return {"found": False}
    rec = dict(rec)
    rec["found"] = True
    return rec


def _search_kb(a: SearchKBArgs, ctx: Context):
    return {"results": ctx.kb.search(a.query, k=3)}


def _find_slots(a: FindSlotsArgs, ctx: Context):
    d0, d1 = date.fromisoformat(a.date_from), date.fromisoformat(a.date_to)
    return {"slots": ctx.calendar.find_slots(d0, d1, a.duration_min)}


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


_CLAIM_WORDS = re.compile(r"\b(₺|\$|€|TL|price|cost|fee|policy|hours|open|close|cancellation|refund|insurance|parking)\b", re.I)


def _draft_reply(a: DraftReplyArgs, ctx: Context):
    valid = ctx.kb.chunk_ids()
    cites = [c for c in a.citations if c in valid]
    unverified = bool(_CLAIM_WORDS.search(a.body)) and not cites
    ctx.draft = Draft(subject=a.subject, body=a.body, citations=cites, unverified_claim=unverified)
    return {"saved": True, "unverified_claim": unverified, "ignored_citations": [c for c in a.citations if c not in valid]}


def _escalate(a: EscalateArgs, ctx: Context):
    ctx.escalation = a.reason
    return {"escalated": True}


def _finish(a: FinishArgs, ctx: Context):
    ctx.finish = a
    return {"done": True}


TOOLS: dict[str, Tool] = {
    "lookup_customer": Tool("lookup_customer", "Find the customer record (and bookings) for an email address.", LookupCustomerArgs, _lookup_customer),
    "search_kb": Tool("search_kb", "Search the clinic knowledge base (hours, prices, policies). Cite the returned chunk_id in drafts.", SearchKBArgs, _search_kb),
    "find_slots": Tool("find_slots", f"List free appointment slots between two dates (default {VISIT_MINUTES} min).", FindSlotsArgs, _find_slots),
    "book_slot": Tool("book_slot", "Book a slot for a customer. Queued for human approval.", BookSlotArgs, _book_slot),
    "cancel_booking": Tool("cancel_booking", "Cancel one of the sender's bookings. Queued for human approval.", CancelBookingArgs, _cancel_booking),
    "draft_reply": Tool("draft_reply", "Save the reply draft (not sent). Include citations for any price/policy statement.", DraftReplyArgs, _draft_reply),
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
