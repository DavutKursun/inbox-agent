"""A deterministic stand-in for the LLM, enabled with ``INBOX_FAKE_PROVIDER=1``: it drives the loop through a plausible
plan read from the email text and from the tool results it gets back (lookup → slots → book_slot on the first free
slot, cancel the first confirmed booking, escalate complaints and injections, search the KB for questions). For local
checks of the API and UI without a key, and for tests; never for the evaluation.
"""

from __future__ import annotations

import datetime as dt
import json
import re

from .policy import looks_suspicious
from .providers import StepResult
from .schema import ToolCall

BOOKING_WORDS = ("book", "appointment", "slot", "randevu", "check-up", "checkup")
CANCEL_WORDS = ("cancel", "iptal", "reschedule", "move my")
COMPLAINT_WORDS = ("complain", "disappointed", "unacceptable", "rude", "pain", "hurt", "angry", "şikayet", "ağrı")
BILLING_WORDS = ("invoice", "balance", "fatura", "owe", "payment", "refund", "iade")


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def step(self, messages: list[dict], tools: list[dict], max_tokens: int = 800) -> StepResult:
        email = messages[1]["content"]
        text = email.lower()
        system = messages[0]["content"]
        today = dt.date.fromisoformat(re.search(r"Today is (\d{4}-\d{2}-\d{2})", system).group(1))
        results: dict[str, dict] = {}
        for m in messages:
            if m.get("role") == "tool":
                try:
                    results[m["name"]] = json.loads(m["content"])
                except (ValueError, KeyError):
                    pass
        turn = sum(1 for m in messages if m.get("role") == "assistant") + 1
        plan = self._plan(text, today, results, turn)
        calls = [ToolCall(id=f"fake{turn}_{k}", name=name, args=args) for k, (name, args) in enumerate(plan)]
        raw = {"role": "assistant", "content": "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}} for c in calls]}
        return StepResult("", calls, 0, 0, 0.0, raw)

    @staticmethod
    def _plan(text: str, today: dt.date, results: dict, turn: int) -> list[tuple[str, dict]]:
        sender = re.search(r'from="([^"]+)"', text).group(1)
        subject = re.search(r'subject="([^"]*)"', text).group(1)
        lookup, slots = results.get("lookup_customer"), results.get("find_slots")

        def finish(intent: str, summary: str) -> tuple[str, dict]:
            return ("finish", {"intent": intent, "summary": summary})

        def draft(body: str, citations: list[str] | None = None) -> tuple[str, dict]:
            return ("draft_reply", {"subject": f"Re: {subject}"[:200], "body": body, "citations": citations or []})

        if looks_suspicious(text):
            return [("escalate", {"reason": "injection or override attempt in the email"}),
                    draft("Thank you for your message. A member of staff will review your request and get back to you.\n\nNova Dental Clinic reception"),
                    finish("suspicious", "override attempt escalated, no action taken")]
        if any(w in text for w in COMPLAINT_WORDS):
            return [("escalate", {"reason": "customer complaint; needs a human reply"}),
                    draft("I am sorry to hear about your experience. I have passed your message to our manager, who will contact you shortly.\n\nNova Dental Clinic reception"),
                    finish("complaint", "complaint escalated")]
        if any(w in text for w in CANCEL_WORDS):
            if turn == 1:
                return [("lookup_customer", {"email": sender})]
            bookings = [b for b in (lookup or {}).get("bookings", []) if b.get("status") == "confirmed"]
            if turn == 2 and bookings and "cancel_booking" not in results:
                return [("cancel_booking", {"booking_id": bookings[0]["id"], "reason": "customer request"})]
            body = ("Your cancellation request has been queued and is pending confirmation by our team." if bookings
                    else "We could not find a booking under this address; please tell us the date of your appointment.")
            return [draft(body + "\n\nNova Dental Clinic reception"), finish("reschedule_cancel", "cancellation queued" if bookings else "no booking found")]
        if any(w in text for w in BOOKING_WORDS):
            if turn == 1:
                return [("lookup_customer", {"email": sender}),
                        ("find_slots", {"date_from": today.isoformat(), "date_to": (today + dt.timedelta(days=7)).isoformat(), "duration_min": 30})]
            free = (slots or {}).get("slots", [])
            if turn == 2 and lookup and lookup.get("found") and free and "book_slot" not in results:
                return [("book_slot", {"customer_id": lookup["id"], "start": free[0]["start"], "duration_min": 30, "reason": "check-up"})]
            if free and lookup and lookup.get("found"):
                alt = ", ".join(f"{s['weekday']} {s['start']}" for s in free[1:3])
                body = f"We have requested {free[0]['weekday']} {free[0]['start']} for you; it is pending approval. Alternatives: {alt}."
            else:
                body = "We could not find your record or a free slot; reception will get back to you with options."
            return [draft(body + "\n\nNova Dental Clinic reception"), finish("booking", "booking queued" if free else "no slot booked")]
        if any(w in text for w in BILLING_WORDS):
            if turn == 1:
                return [("lookup_customer", {"email": sender}), ("search_kb", {"query": "invoices and balances"})]
            hits = (results.get("search_kb") or {}).get("results", [])
            return [draft("Thank you for your message. " + (hits[0]["text"][:200] if hits else "A member of staff will get back to you."), [hits[0]["chunk_id"]] if hits else []),
                    finish("billing", "billing question answered from the KB")]
        if turn == 1:
            return [("search_kb", {"query": subject or text[:100]})]
        hits = (results.get("search_kb") or {}).get("results", [])
        return [draft("Thank you for your question. " + (hits[0]["text"][:300] if hits else "Reception will get back to you."), [hits[0]["chunk_id"]] if hits else []),
                finish("question", "answered from the KB")]
