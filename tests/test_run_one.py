from __future__ import annotations

import json

from inbox_agent.costs import BudgetGuard
from inbox_agent.providers import ProviderError
from scripts.run_one import main as run_one
from tests.conftest import ScriptedProvider


class Failing:
    name = model = "failing"

    def step(self, *_a, **_k):
        raise ProviderError("HTTP 401: invalid api key")


def test_run_one_exit_codes(world, tmp_path, capsys):
    mail = tmp_path / "mail.json"
    mail.write_text(json.dumps({"from": "ada@example.com", "subject": "hi", "body": "when are you open?"}), encoding="utf-8")
    budget = BudgetGuard(tmp_path / "budget.json", 1.0, 10)  # never the project's .budget.json
    argv = [str(mail), "--today", "2026-10-12"]

    assert run_one(argv, provider=Failing(), ctx_factory=world["factory"], budget=budget) == 4
    out = capsys.readouterr().out
    assert "=== unfinished  (error" in out and "HTTP 401" in out and out.rstrip().endswith("(see ESCALATED above)")

    ok = ScriptedProvider([[("search_kb", {"query": "opening hours"}), ("finish", {"intent": "question", "summary": "hours answered"})]])
    assert run_one(argv, provider=ok, ctx_factory=world["factory"], budget=budget) == 0
    out = capsys.readouterr().out
    assert "=== question  (finish, 1 turns" in out and "[ok     ] search_kb" in out and budget.read().calls == 1
