"""Markdown report for an eval run: the metrics table, the README row, and the worst scenarios with trace links.

Usage: python scripts/eval_report.py [--model claude-haiku-5-5] [--worst 10] [--results-dir eval/results]
--model defaults to the configured provider's model.
Writes eval/results/<model>-report.md and prints it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from inbox_agent.config import CRM_DB, RESULTS, settings
from inbox_agent.crm import CRM
from inbox_agent.evaluation import (
    active_model,
    markdown_table,
    read_records,
    readme_row,
    report_path,
    rescore,
    results_path,
    summarize,
    worst,
)
from inbox_agent.scenarios import REVIEWED_PATH, read_jsonl


def build_report(scenarios, records: list[dict], model: str, results_dir: Path, n_worst: int = 10, crm: CRM | None = None) -> str:
    records = rescore(scenarios, records, crm)
    summary = summarize(scenarios, records, model)
    lines = [f"# Eval report: {model}", "", markdown_table(summary), ""]
    if summary.get("n"):
        lines += ["README row:", "", readme_row(summary), ""]
    if summary.get("n"):
        lines += [f"## Worst {min(n_worst, len(records))} scenarios (by failed checks)", "", "| id | group | intent (expected) | failed checks | manual | trace |", "|---|---|---|---|---|---|"]
        for r in worst(records, n_worst):
            trace = Path(r["trace_path"])
            try:
                link = trace.relative_to(results_dir)
            except ValueError:
                link = trace
            lines.append(f"| {r['id']} | {r['group']} | {r['intent']} ({r['expected_intent']}) | {', '.join(r['failed']) or '-'} | "
                         f"{', '.join(r.get('manual', [])) or '-'} | [{trace.name}]({link}) |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, *, scenarios=None) -> int:
    ap = argparse.ArgumentParser(description="Write the markdown report for an eval run.")
    ap.add_argument("--model", default=active_model(settings), help="default: the configured provider's model")
    ap.add_argument("--worst", type=int, default=10)
    ap.add_argument("--results-dir", default=str(RESULTS))
    args = ap.parse_args(argv)
    results_dir = Path(args.results_dir)
    scenarios = scenarios if scenarios is not None else read_jsonl(REVIEWED_PATH)
    records = read_records(results_path(args.model, results_dir))
    text = build_report(scenarios, records, args.model, results_dir, args.worst, CRM(CRM_DB) if CRM_DB.exists() else None)
    report_path(args.model, results_dir).parent.mkdir(parents=True, exist_ok=True)
    report_path(args.model, results_dir).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
