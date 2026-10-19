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

# Phrases in the email that trigger "suspicious" handling: the agent may read, search and draft, but any
# approval-level action is refused outright for the thread (not just queued) and the thread is escalated.
# Only explicit attempts to override the instructions are listed. Ordinary words ("refund", "override",
# "disregard", "you are now") are not markers: a refund request is handled by policy — there is no refund
# tool, so the model has to escalate — and a legitimate customer must not get flagged for using them.
INJECTION_MARKERS = (
    "ignore previous instructions", "ignore all previous", "as the system", "system prompt", "developer message", "admin mode",
    # Turkish
    "önceki talimatları yok say", "sistem olarak",
)


# Per-thread call limits, enforced by the loop: a call over the limit is answered with an error and not run. Empty in
# the deployed version (v2, prompt 2026-10-19.1), which was evaluated without one. v4 (prompt 2026-10-19.3) ran with
# {"search_kb": 2}: it ended the runaway searching of v3 but lost on intent accuracy, see DESIGN.md "Findings".
CALL_LIMITS: dict[str, int] = {}


def call_limit(tool: str) -> int | None:
    return CALL_LIMITS.get(tool)


def risk_of(tool: str) -> Risk:
    return POLICY.get(tool, "forbidden")


def _fold(text: str) -> str:
    """Case-fold with the Turkish dotted/dotless i collapsed, so "TALİMATLARI" matches "talimatları"."""
    return text.replace("İ", "i").replace("ı", "i").lower()


_FOLDED_MARKERS = tuple(_fold(m) for m in INJECTION_MARKERS)


def looks_suspicious(text: str) -> bool:
    t = _fold(text)
    return any(m in t for m in _FOLDED_MARKERS)


def policy_summary() -> str:
    auto = ", ".join(t for t, r in POLICY.items() if r == "auto")
    appr = ", ".join(t for t, r in POLICY.items() if r == "approval")
    return (f"Tools that run immediately: {auto}. Tools that are queued for human approval: {appr} "
            "(call them when appropriate; you will receive 'queued' and must tell the customer it is pending). "
            "You cannot send email, issue refunds, change balances or delete records; for those, call escalate.")
