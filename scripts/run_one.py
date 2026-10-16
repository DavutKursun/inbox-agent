"""Run the agent on one email JSON file ({"from","subject","body"}) and print the result + trace.

Usage: python scripts/run_one.py data/samples/booking.json [--today 2026-10-13]
Exit codes: 0 ok, 2 not configured, 4 API error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from inbox_agent.agent import Agent  # noqa: E402
from inbox_agent.calendar import FakeCalendar  # noqa: E402
from inbox_agent.config import settings  # noqa: E402
from inbox_agent.costs import BudgetGuard, format_cost  # noqa: E402
from inbox_agent.crm import CRM  # noqa: E402
from inbox_agent.kb import KnowledgeBase  # noqa: E402
from inbox_agent.providers import ProviderError, get_provider  # noqa: E402
from inbox_agent.schema import Email  # noqa: E402
from inbox_agent.store import Store  # noqa: E402
from inbox_agent.tools import Context  # noqa: E402


def default_ctx_factory(today: dt.date | None = None):
    kb = KnowledgeBase()
    store = Store()

    def make(thread_id: str, customer_email: str) -> Context:
        return Context(crm=CRM(), calendar=FakeCalendar(today=today), kb=kb, store=store, thread_id=thread_id, customer_email=customer_email)

    return make


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--today", default=None)
    args = ap.parse_args()
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    try:
        provider = get_provider(settings)
    except ProviderError as e:
        print("not configured:", e)
        return 2
    email = Email.model_validate(json.loads(Path(args.file).read_text(encoding="utf-8")))
    agent = Agent(provider, default_ctx_factory(today), budget=BudgetGuard(settings.budget_file, settings.max_daily_usd), today=today)
    try:
        res = agent.handle(email)
    except ProviderError as e:
        print("API error:", e)
        return 4
    print(f"\n=== {res.intent}  ({res.stopped_reason}, {res.turns} turns, {res.input_tokens}+{res.output_tokens} tokens, "
          f"{format_cost(res.model, res.cost_usd)}, {res.latency_ms:.0f} ms)")
    print("summary:", res.summary)
    for ev in res.actions_taken:
        print(f"  [{ev.status:7s}] {ev.call.name}({json.dumps(ev.call.args, ensure_ascii=False)[:120]})")
    for a in res.actions_pending:
        print(f"  [PENDING] {a.action_id} {a.tool} {a.args}")
    if res.escalated:
        print("ESCALATED:", res.escalation_reason)
    if res.draft_reply:
        d = res.draft_reply
        print(f"\n--- draft: {d.subject}  (citations: {d.citations}{', UNVERIFIED CLAIM' if d.unverified_claim else ''})\n{d.body}")
    print("\ntrace:", agent.ctx_factory(res.thread_id, email.from_email).store.trace_path(res.thread_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
