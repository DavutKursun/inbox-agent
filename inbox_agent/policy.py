"""The safety policy: which tools run automatically, which wait for a human, which are refused.

This is a table, not prompt wording. The agent loop consults it before executing anything.
Anything not listed is forbidden.
"""

from __future__ import annotations

from .schema import Risk

POLICY: dict[str, Risk] = {
    "lookup_customer": "auto",
    "search_kb": "auto",
    "find_slots": "auto",
    "draft_reply": "auto",
    "escalate": "auto",
    "finish": "auto",
    "book_slot": "approval",
    "cancel_booking": "approval",
    # Deliberately absent → forbidden: send_email, refund, change_balance, delete_customer
}

# Words in the email that trigger "suspicious" handling: the agent may read, search and draft, but any
# approval-level action is refused outright for the thread (not just queued) and the thread is escalated.
INJECTION_MARKERS = (
    "ignore previous instructions", "ignore all previous", "as the system", "you are now", "system prompt",
    "developer message", "refund", "override", "admin mode", "disregard",
)


def risk_of(tool: str) -> Risk:
    return POLICY.get(tool, "forbidden")


def looks_suspicious(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in INJECTION_MARKERS)


def policy_summary() -> str:
    auto = ", ".join(t for t, r in POLICY.items() if r == "auto")
    appr = ", ".join(t for t, r in POLICY.items() if r == "approval")
    return (f"Tools that run immediately: {auto}. Tools that are queued for human approval: {appr} "
            "(call them when appropriate; you will receive 'queued' and must tell the customer it is pending). "
            "You cannot send email, issue refunds, change balances or delete records; for those, call escalate.")
