"""The evaluation scenario set (§6): 120 emails with expected outcomes, stored as JSONL under data/scenarios/.

A scenario is an email plus what a correct run of the agent does with it. `scripts/make_scenarios.py` drafts them
with the LLM (``reviewed`` false, the expected block is the LLM's proposal); `scripts/review_scenarios.py` prints
them for Davut and applies his overrides, writing ``scenarios.jsonl`` with ``reviewed`` true. The eval reads only
reviewed scenarios.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import SCENARIOS
from .crm import CRM
from .schema import Email, FinishIntent
from .tools import TOOLS

Group = Literal["booking", "reschedule_cancel", "question", "billing", "complaint", "adversarial"]
GROUPS: tuple[str, ...] = ("booking", "reschedule_cancel", "question", "billing", "complaint", "adversarial")
Language = Literal["en", "tr"]
ABSENT_TOOLS = ("refund", "send_email", "change_balance", "delete_customer")  # forbidden by absence (see policy.py)
KNOWN_TOOLS = set(TOOLS) | set(ABSENT_TOOLS)
DRAFT_PATH = SCENARIOS / "draft.jsonl"
REVIEWED_PATH = SCENARIOS / "scenarios.jsonl"


class Expected(BaseModel):
    """What a correct run looks like. Tool names are checked against the registry (plus the tools that exist only as
    forbidden-by-absence names), and a tool cannot be both required and forbidden."""

    model_config = ConfigDict(extra="forbid")
    intent: FinishIntent
    required_tools: list[str] = Field(default_factory=list, description="tools a correct run must call")
    forbidden_tools: list[str] = Field(default_factory=list, description="tools a correct run must not call at all (a refused call still counts as a call)")
    must_escalate: bool = False
    approval_expected: bool = False  # a book_slot or cancel_booking should end up queued for approval
    draft_must_cite: bool = False  # the reply states a price, hours or policy and must cite a KB chunk
    notes: str = Field(default="", max_length=500, description="one line on what matters in this scenario")

    @field_validator("required_tools", "forbidden_tools")
    @classmethod
    def _known_tools(cls, v: list[str]) -> list[str]:
        unknown = [t for t in v if t not in KNOWN_TOOLS]
        if unknown:
            raise ValueError(f"unknown tools {unknown}; known: {sorted(KNOWN_TOOLS)}")
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _no_overlap(self) -> Expected:
        overlap = set(self.required_tools) & set(self.forbidden_tools)
        if overlap:
            raise ValueError(f"tools both required and forbidden: {sorted(overlap)}")
        return self


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^(booking|reschedule_cancel|question|billing|complaint|adversarial)-\d{2}$")
    group: Group
    language: Language
    email: Email
    expected: Expected
    unknown_sender: bool = False  # the address is deliberately not in the CRM
    reviewed: bool = False

    @model_validator(mode="after")
    def _id_matches_group(self) -> Scenario:
        if not self.id.startswith(self.group + "-"):
            raise ValueError(f"id {self.id!r} does not belong to group {self.group!r}")
        return self

    def to_json(self) -> str:
        return json.dumps(self.model_dump(by_alias=True), ensure_ascii=False)


# --- files -----------------------------------------------------------------------
def read_jsonl(path: Path | str) -> list[Scenario]:
    p = Path(path)
    if not p.exists():
        return []
    return [Scenario.model_validate_json(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path | str, scenarios: list[Scenario]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(s.to_json() + "\n" for s in scenarios), encoding="utf-8")


def check_senders(scenarios: list[Scenario], crm: CRM) -> list[str]:
    """Known senders must exist in the CRM; scenarios marked ``unknown_sender`` must not. Returns the problems."""
    problems = []
    for s in scenarios:
        known = crm.lookup(s.email.from_email) is not None
        if s.unknown_sender and known:
            problems.append(f"{s.id}: marked unknown_sender but {s.email.from_email} exists in the CRM")
        if not s.unknown_sender and not known:
            problems.append(f"{s.id}: sender {s.email.from_email} is not in the CRM")
    duplicates = {s.id for s in scenarios if sum(t.id == s.id for t in scenarios) > 1}
    problems += [f"duplicate id {d}" for d in sorted(duplicates)]
    return problems


# --- review overrides ----------------------------------------------------------------
def _parse_value(current: Any, raw: str) -> Any:
    """Type-directed: a string field keeps the raw text; lists accept JSON or comma-separated names; others take JSON."""
    raw = raw.strip()
    if isinstance(current, str):
        return raw
    if isinstance(current, list):
        if raw.startswith("["):
            return json.loads(raw)
        return [x.strip() for x in raw.split(",") if x.strip()]
    if isinstance(current, bool):
        if raw.lower() in ("true", "1", "yes"):
            return True
        if raw.lower() in ("false", "0", "no"):
            return False
        raise ValueError(f"not a boolean: {raw!r}")
    return json.loads(raw)


def apply_overrides(scenarios: list[Scenario], rows: list[dict]) -> list[Scenario]:
    """Apply review rows ``{"id", "field", "value"}``: ``field`` is a dotted path into the scenario (``expected.intent``,
    ``expected.required_tools``, ``email.subject``, ``language``, ``unknown_sender`` …) or ``drop`` (value true removes
    the scenario). Every change is re-validated. Unknown ids or fields raise."""
    by_id = {s.id: s.model_dump(by_alias=True) for s in scenarios}
    dropped: set[str] = set()
    for row in rows:
        sid, field, value = row["id"].strip(), row["field"].strip(), row["value"]
        if sid not in by_id:
            raise KeyError(f"unknown scenario id {sid!r}")
        if field == "drop":
            if _parse_value(False, value):
                dropped.add(sid)
            continue
        data, parts = by_id[sid], field.split(".")
        node = data
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"{sid}: unknown field {field!r}")
            node = node[part]
        leaf = parts[-1]
        if not isinstance(node, dict) or leaf not in node:
            raise KeyError(f"{sid}: unknown field {field!r}")
        node[leaf] = _parse_value(node[leaf], value)
        by_id[sid] = Scenario.model_validate(data).model_dump(by_alias=True)  # validate now, keep the dict for further rows
    return [Scenario.model_validate(d) for sid, d in by_id.items() if sid not in dropped]


def read_overrides(path: Path | str) -> list[dict]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    missing = [r for r in rows if not {"id", "field", "value"} <= set(r)]
    if missing:
        raise ValueError("overrides CSV needs the columns id, field, value")
    return rows


def mark_reviewed(scenarios: list[Scenario]) -> list[Scenario]:
    return [s.model_copy(update={"reviewed": True}) for s in scenarios]
