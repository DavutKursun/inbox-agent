"""Run the reviewed scenarios through the agent and write the metrics (Milestone 3, step 3).

Usage: python scripts/eval.py [--group booking,question] [--limit N] [--ids a,b] [--delay S] [--no-pacing] [--dry-run]

Reads data/scenarios/scenarios.jsonl (reviewed only), builds fresh fixtures in a temp dir for the run, appends one record
per scenario to eval/results/<model>.jsonl (resumable: ids already there are skipped), keeps output tokens under the
free tier's 1,000 per minute, waits a minute on a per-minute 429, and stops with exit 3 on a daily quota or budget
stop, printing progress. After the run it recomputes the summary for every record in the file and writes
eval/results/<model>-summary.json and .md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from inbox_agent.config import RESULTS, fixtures_today, settings
from inbox_agent.costs import BudgetGuard
from inbox_agent.evaluation import Pacer, markdown_table, read_records, results_path, run, select, summarize, summary_path
from inbox_agent.providers import ProviderError, get_provider
from inbox_agent.scenarios import GROUPS, REVIEWED_PATH, read_jsonl


def write_summary(scenarios, model: str, results_dir: Path) -> dict:
    summary = summarize(scenarios, read_records(results_path(model, results_dir)), model)
    results_dir.mkdir(parents=True, exist_ok=True)
    summary_path(model, results_dir).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    summary_path(model, results_dir, "md").write_text(markdown_table(summary) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None, *, provider=None, scenarios=None, budget: BudgetGuard | None = None, sleep=None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate the agent on the reviewed scenarios (resumable).")
    ap.add_argument("--group", default=None, help="comma-separated groups")
    ap.add_argument("--limit", type=int, default=None, help="at most N scenarios (after resume filtering is NOT applied: N of the selection)")
    ap.add_argument("--ids", default=None, help="comma-separated scenario ids")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait between emails, on top of the token pacing")
    ap.add_argument("--no-pacing", action="store_true", help="do not pace output tokens (paid tiers)")
    ap.add_argument("--results-dir", default=str(RESULTS))
    ap.add_argument("--dry-run", action="store_true", help="list the selected scenarios, no calls")
    args = ap.parse_args(argv)
    results_dir = Path(args.results_dir)
    scenarios = scenarios if scenarios is not None else [s for s in read_jsonl(REVIEWED_PATH) if s.reviewed]
    groups = [g for g in args.group.split(",") if g] if args.group else None
    if groups and (bad := [g for g in groups if g not in GROUPS]):
        print("unknown group(s):", bad)
        return 1
    chosen = select(scenarios, groups, [i for i in args.ids.split(",") if i] if args.ids else None, args.limit)
    if args.dry_run:
        print(f"{len(chosen)} scenario(s) selected: {', '.join(s.id for s in chosen)}")
        return 0
    if provider is None:
        try:
            provider = get_provider(settings)
        except ProviderError as e:
            print("not configured:", e)
            return 2
    if budget is None:
        budget = BudgetGuard(settings.budget_file, settings.max_daily_usd, settings.max_daily_calls)
    kwargs = {"sleep": sleep} if sleep is not None else {}
    stats = run(chosen, provider, today=fixtures_today(), results_dir=results_dir, delay=args.delay, pacer=None if args.no_pacing else Pacer(), budget=budget, **kwargs)
    summary = write_summary(scenarios, provider.model, results_dir)
    print(f"\nthis run: {stats.ran} ran, {stats.skipped} already done, {stats.total} selected; file: {summary.get('n', 0)} records")
    print(markdown_table(summary))
    if stats.stopped:
        print(f"\nSTOPPED ({stats.stopped}): {stats.message}\nresume later with the same command.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
