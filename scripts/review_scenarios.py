"""Review the drafted scenarios and apply Davut's overrides (Milestone 3, step 1).

Usage:
  python scripts/review_scenarios.py                          # print every group of data/scenarios/draft.jsonl as tables
  python scripts/review_scenarios.py --group adversarial      # one group
  python scripts/review_scenarios.py --apply overrides.csv    # merge the overrides, mark reviewed, write scenarios.jsonl
  python scripts/review_scenarios.py --approve                # no overrides: accept the drafts as reviewed

overrides.csv has the columns id,field,value. ``field`` is a dotted path into the scenario: expected.intent,
expected.required_tools, expected.forbidden_tools, expected.must_escalate, expected.approval_expected,
expected.draft_must_cite, expected.notes, email.subject, email.body, language, unknown_sender; or ``drop`` with value
true to remove the scenario. Lists as JSON (["search_kb"]) or comma-separated (lookup_customer, search_kb).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from inbox_agent.scenarios import (
    DRAFT_PATH,
    GROUPS,
    REVIEWED_PATH,
    Scenario,
    apply_overrides,
    mark_reviewed,
    read_jsonl,
    read_overrides,
    write_jsonl,
)

BODY_CHARS = 120


def _cell(text: str, limit: int | None = None) -> str:
    text = " ".join(text.split()).replace("|", "\\|")
    return text if limit is None or len(text) <= limit else text[:limit].rstrip() + "…"


def table(scenarios: list[Scenario]) -> str:
    """One compact markdown table: id, language, subject, start of the body, and the proposed expected block."""
    rows = ["| id | lang | subject | body (first 120 chars) | intent | tools (required; !forbidden) | esc | appr | cite |",
            "|---|---|---|---|---|---|---|---|---|"]
    for s in scenarios:
        e = s.expected
        tools = ", ".join(e.required_tools) + (" ; !" + ", !".join(e.forbidden_tools) if e.forbidden_tools else "")
        flag = " (unknown sender)" if s.unknown_sender else ""
        rows.append(f"| {s.id} | {s.language} | {_cell(s.email.subject, 60)}{flag} | {_cell(s.email.body, BODY_CHARS)} | {e.intent} | {tools or '-'} | "
                    f"{'yes' if e.must_escalate else 'no'} | {'yes' if e.approval_expected else 'no'} | {'yes' if e.draft_must_cite else 'no'} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Print scenario drafts for review, or apply overrides and write the reviewed set.")
    ap.add_argument("--in", dest="src", default=str(DRAFT_PATH))
    ap.add_argument("--out", default=str(REVIEWED_PATH))
    ap.add_argument("--group", choices=GROUPS, default=None)
    ap.add_argument("--apply", metavar="OVERRIDES_CSV", default=None)
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--reviewed-by", default="", help="recorded in every scenario written with --apply / --approve")
    args = ap.parse_args(argv)
    scenarios = read_jsonl(args.src)
    if not scenarios:
        print(f"no scenarios in {args.src}")
        return 1
    if args.apply or args.approve:
        rows = read_overrides(args.apply) if args.apply else []
        merged = mark_reviewed(apply_overrides(scenarios, rows), by=args.reviewed_by)
        write_jsonl(args.out, merged)
        print(f"{len(rows)} override(s) applied, {len(scenarios) - len(merged)} dropped; {len(merged)} reviewed scenarios written to {args.out}")
        return 0
    for g in [args.group] if args.group else GROUPS:
        group = [s for s in scenarios if s.group == g]
        if not group:
            continue
        print(f"\n### {g} ({len(group)}; {sum(s.language == 'tr' for s in group)} tr, {sum(s.unknown_sender for s in group)} unknown sender)\n")
        print(table(group))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
