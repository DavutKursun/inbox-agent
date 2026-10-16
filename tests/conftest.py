from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from inbox_agent.calendar import FakeCalendar  # noqa: E402
from inbox_agent.crm import CRM  # noqa: E402
from inbox_agent.kb import KnowledgeBase  # noqa: E402
from inbox_agent.providers import StepResult  # noqa: E402
from inbox_agent.schema import ToolCall  # noqa: E402
from inbox_agent.store import Store  # noqa: E402
from inbox_agent.tools import Context  # noqa: E402

TODAY = dt.date(2026, 10, 12)  # a Monday


class ScriptedProvider:
    """Plays a fixed list of turns; each turn is a list of (tool_name, args). Records messages it saw."""

    name = "scripted"
    model = "scripted-model"

    def __init__(self, turns: list[list[tuple[str, dict]]]) -> None:
        self.turns = turns
        self.seen: list[list[dict]] = []
        self._i = 0

    def step(self, messages, tools, max_tokens=800):
        self.seen.append([dict(m) for m in messages])
        calls = self.turns[min(self._i, len(self.turns) - 1)]
        self._i += 1
        tcs = [ToolCall(id=f"c{self._i}_{k}", name=n, args=a) for k, (n, a) in enumerate(calls)]
        raw = {"role": "assistant", "content": "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}} for c in tcs]}
        return StepResult("", tcs, 500, 60, 0.01, raw)


@pytest.fixture
def world(tmp_path):
    crm = CRM(tmp_path / "crm.sqlite")
    cid = crm.add_customer("Ada Yılmaz", "ada@example.com", "0532", "2026-03-01", 0.0)
    other = crm.add_customer("Mert Kaya", "mert@example.com", "0533", "2026-01-15", 1500.0)
    cal = FakeCalendar(tmp_path / "cal.json", today=TODAY)
    cal.busy = [{"id": "evt_ada", "start": "2026-10-14T10:00", "duration_min": 30, "title": "Ada"}]
    cal.save()
    crm.add_booking("evt_ada", cid, "2026-10-14T10:00", 30, "visit")
    kb = KnowledgeBase(ROOT / "data" / "kb")
    store = Store(tmp_path / "runtime")

    def factory(thread_id, customer_email):
        return Context(crm=CRM(tmp_path / "crm.sqlite"), calendar=FakeCalendar(tmp_path / "cal.json", today=TODAY), kb=kb, store=store,
                       thread_id=thread_id, customer_email=customer_email)

    return {"crm": crm, "cal": cal, "kb": kb, "store": store, "factory": factory, "ada": cid, "mert": other}
