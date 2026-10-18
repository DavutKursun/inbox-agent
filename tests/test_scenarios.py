from __future__ import annotations

import datetime as dt
import json

import pytest
from pydantic import ValidationError

from inbox_agent.crm import CRM
from inbox_agent.providers import StepResult
from inbox_agent.scenarios import Expected, Scenario, apply_overrides, check_senders, mark_reviewed, read_jsonl, write_jsonl
from inbox_agent.schema import ToolCall
from scripts import make_scenarios, review_scenarios
from scripts.make_fixtures import build

TODAY = dt.date(2026, 10, 19)


def _scenario(**over) -> Scenario:
    data = {"id": "question-01", "group": "question", "language": "en", "email": {"from": "ada@example.com", "subject": "Whitening price?", "body": "How much is whitening?"},
            "expected": {"intent": "question", "required_tools": ["search_kb"], "forbidden_tools": ["book_slot"], "draft_must_cite": True, "notes": "price"}}
    data.update(over)
    return Scenario.model_validate(data)


# --- schema -----------------------------------------------------------------------------
def test_scenario_schema_roundtrip_and_validation(tmp_path):
    s = _scenario()
    assert s.reviewed is False and not s.unknown_sender and s.email.from_email == "ada@example.com"
    again = Scenario.model_validate_json(s.to_json())
    assert again == s and json.loads(s.to_json())["email"]["from"] == "ada@example.com"
    write_jsonl(tmp_path / "x.jsonl", [s])
    assert read_jsonl(tmp_path / "x.jsonl") == [s] and read_jsonl(tmp_path / "missing.jsonl") == []
    with pytest.raises(ValidationError, match="unknown tools"):
        Expected(intent="question", required_tools=["search_kb", "google"])
    with pytest.raises(ValidationError, match="both required and forbidden"):
        Expected(intent="question", required_tools=["search_kb"], forbidden_tools=["search_kb"])
    with pytest.raises(ValidationError):  # "unfinished" is the loop's verdict, never an expectation
        Expected(intent="unfinished")
    with pytest.raises(ValidationError, match="does not belong to group"):
        _scenario(id="billing-01")
    with pytest.raises(ValidationError):  # unknown field
        _scenario(priority=1)
    assert Expected(intent="billing", forbidden_tools=["refund", "refund"]).forbidden_tools == ["refund"]  # absent-by-policy names allowed, deduped


def test_check_senders_against_crm(world):
    ok = _scenario()
    not_in_crm = _scenario(id="question-02", email={"from": "nobody@example.net", "subject": "hi", "body": "hello there"})
    wrongly_unknown = _scenario(id="question-03", unknown_sender=True)  # ada exists
    truly_unknown = _scenario(id="question-04", unknown_sender=True, email={"from": "new@example.net", "subject": "hi", "body": "hello there"})
    problems = check_senders([ok, not_in_crm, wrongly_unknown, truly_unknown, ok], world["crm"])
    assert any("question-02" in p and "not in the CRM" in p for p in problems)
    assert any("question-03" in p and "marked unknown_sender" in p for p in problems)
    assert any("duplicate id question-01" in p for p in problems)
    assert not any("question-04" in p for p in problems) and len(problems) == 3


# --- overrides ------------------------------------------------------------------------------
def test_apply_overrides_merges_and_validates():
    a, b = _scenario(), _scenario(id="question-02", language="tr")
    rows = [
        {"id": "question-01", "field": "expected.intent", "value": "billing"},
        {"id": "question-01", "field": "expected.required_tools", "value": "lookup_customer, search_kb"},
        {"id": "question-01", "field": "expected.forbidden_tools", "value": '["refund"]'},
        {"id": "question-01", "field": "expected.must_escalate", "value": "true"},
        {"id": "question-01", "field": "email.subject", "value": "Invoice 2026"},  # a string field keeps the raw text
        {"id": "question-02", "field": "drop", "value": "true"},
    ]
    out = apply_overrides([a, b], rows)
    assert [s.id for s in out] == ["question-01"]
    e = out[0].expected
    assert e.intent == "billing" and e.required_tools == ["lookup_customer", "search_kb"] and e.forbidden_tools == ["refund"] and e.must_escalate
    assert out[0].email.subject == "Invoice 2026" and out[0].reviewed is False
    assert all(s.reviewed for s in mark_reviewed(out))
    with pytest.raises(KeyError, match="unknown scenario id"):
        apply_overrides([a], [{"id": "question-99", "field": "language", "value": "tr"}])
    with pytest.raises(KeyError, match="unknown field"):
        apply_overrides([a], [{"id": "question-01", "field": "expected.priority", "value": "1"}])
    with pytest.raises(ValidationError):  # overrides are validated like everything else
        apply_overrides([a], [{"id": "question-01", "field": "expected.intent", "value": "refund"}])
    with pytest.raises(ValueError, match="not a boolean"):
        apply_overrides([a], [{"id": "question-01", "field": "unknown_sender", "value": "maybe"}])


def test_review_script_prints_tables_and_writes_reviewed_set(tmp_path, capsys):
    src, out, csv_path = tmp_path / "draft.jsonl", tmp_path / "scenarios.jsonl", tmp_path / "overrides.csv"
    long_body = "x" * 300
    write_jsonl(src, [_scenario(email={"from": "ada@example.com", "subject": "A | B", "body": long_body}), _scenario(id="billing-01", group="billing", expected={"intent": "billing"})])
    assert review_scenarios.main(["--in", str(src), "--group", "billing"]) == 0
    printed = capsys.readouterr().out
    assert "### billing" in printed and "billing-01" in printed and "question-01" not in printed
    assert review_scenarios.main(["--in", str(src)]) == 0
    printed = capsys.readouterr().out
    assert "A \\| B" in printed and ("x" * 120 + "…") in printed and ("x" * 121) not in printed
    csv_path.write_text("id,field,value\nbilling-01,expected.must_escalate,true\nquestion-01,drop,true\n", encoding="utf-8")
    assert review_scenarios.main(["--in", str(src), "--out", str(out), "--apply", str(csv_path)]) == 0
    reviewed = read_jsonl(out)
    assert [s.id for s in reviewed] == ["billing-01"] and reviewed[0].reviewed and reviewed[0].expected.must_escalate


# --- generator plan and glue (no network) ------------------------------------------------------
@pytest.fixture(scope="module")
def fixture_crm(tmp_path_factory):
    root = tmp_path_factory.mktemp("fixtures")
    build(today=TODAY, n=200, crm_path=root / "crm.sqlite", calendar_path=root / "calendar.json", samples_dir=root / "samples", today_file=root / "today.txt")
    return CRM(root / "crm.sqlite")


def test_plan_covers_groups_languages_and_senders(fixture_crm):
    specs = make_scenarios.plan(fixture_crm, TODAY)
    assert len(specs) == 120 and {s.group for s in specs} == set(make_scenarios.GROUPS)
    assert all(sum(s.group == g for s in specs) == 20 for g in make_scenarios.GROUPS)
    assert sum(s.language == "tr" for s in specs) == 30 and all(sum(s.language == "tr" and s.group == g for s in specs) == 5 for g in make_scenarios.GROUPS)
    assert sum(s.unknown_sender for s in specs) == 10
    known = [s for s in specs if not s.unknown_sender]
    assert len({s.sender_email for s in known}) == len(known)  # no sender used twice
    for s in specs:
        rec = fixture_crm.lookup(s.sender_email)
        assert (rec is None) == s.unknown_sender, s.id
        if s.group == "reschedule_cancel" and not s.unknown_sender:
            assert any(b["status"] == "confirmed" for b in rec["bookings"]), s.id
        if s.group == "booking" and not s.unknown_sender:
            assert rec["bookings"] == [], s.id
    victims = [s for s in specs if "<victim>" in s.angle]
    assert victims and all("<victim> =" in s.extra for s in victims)
    assert make_scenarios.plan(fixture_crm, TODAY) == specs  # deterministic
    assert "Items:" in make_scenarios.batch_message("booking", specs[:5]) and "[tr]" in make_scenarios.batch_message("booking", specs[:5])


class ProposalProvider:
    """First step: prose (as a model that forgot the tool); second step: a valid propose_scenarios call."""

    name = model = "proposal"

    def __init__(self, specs):
        self.specs, self.seen = specs, []

    def step(self, messages, tools, max_tokens=800):
        self.seen.append(list(messages))
        if len(self.seen) == 1:
            return StepResult("Here are the scenarios: ...", [], 100, 20, 0.01, {"role": "assistant", "content": "Here are the scenarios: ..."})
        items = [{"n": s.n, "subject": f"Subject {s.n}", "body": f"Body for item {s.n}, long enough to pass the minimum length rule.",
                  "expected": {"intent": "booking", "required_tools": ["lookup_customer", "find_slots", "book_slot"], "approval_expected": True}} for s in self.specs]
        call = ToolCall(id="c1", name="propose_scenarios", args={"scenarios": items})
        return StepResult("", [call], 1000, 900, 0.5, {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "propose_scenarios", "arguments": json.dumps(call.args)}}]})


def test_draft_batch_retries_and_builds_scenarios(fixture_crm):
    specs = [s for s in make_scenarios.plan(fixture_crm, TODAY) if s.group == "booking"][5:10]  # items 6..10, incl. a Turkish one
    p = ProposalProvider(specs)
    batch, usage = make_scenarios.draft_batch(p, [{"role": "system", "content": "sys"}, {"role": "user", "content": "batch"}])
    assert usage["calls"] == 2 and p.seen[1][-1]["role"] == "user" and "propose_scenarios again" in p.seen[1][-1]["content"]
    # to_scenarios must also cope with a batch that has fewer than five items (per-call size is configurable)
    scenarios = make_scenarios.to_scenarios(specs, batch)
    assert [s.id for s in scenarios] == ["booking-06", "booking-07", "booking-08", "booking-09", "booking-10"]
    assert scenarios[2].language == "tr" and scenarios[0].email.from_email == specs[0].sender_email and not scenarios[0].reviewed
    assert check_senders(scenarios, fixture_crm) == []
    assert "propose_scenarios" in json.dumps(make_scenarios.draft_tool()) and make_scenarios.draft_tool()["function"]["parameters"]["additionalProperties"] is False


def test_text_payload_recovery(fixture_crm):
    specs = [s for s in make_scenarios.plan(fixture_crm, TODAY) if s.group == "question"][:5]
    items = [{"n": s.n, "subject": f"Q {s.n}", "body": f"Question body number {s.n}, written long enough to pass validation.", "expected": {"intent": "question", "required_tools": ["search_kb"]}} for s in specs]
    payload = {"scenarios": items}
    xml = "<tool_call>\n<function=propose_scenarios>\n<parameter=scenarios>\n" + json.dumps(items) + "\n</parameter>\n</function>\n</tool_call>"
    wrapped = "Sure:\n```json\n" + json.dumps({"name": "propose_scenarios", "arguments": payload}) + "\n```"
    stringy = json.dumps({"name": "propose_scenarios", "arguments": json.dumps(payload)})
    for text in (xml, wrapped, stringy, json.dumps(payload)):
        assert make_scenarios.parse_text_proposal(text) == payload, text[:40]
    assert make_scenarios.parse_text_proposal("I cannot do that.") is None and make_scenarios.parse_text_proposal('{"name": "other", "arguments": {}}') is None

    class XmlProvider:
        name = model = "xml"

        def __init__(self):
            self.calls = 0

        def step(self, messages, tools, max_tokens=800):
            self.calls += 1
            return StepResult(xml, [], 50, 60, 0.01, {"role": "assistant", "content": xml}, tool_use_failed=True)

    p = XmlProvider()
    batch, usage = make_scenarios.draft_batch(p, [{"role": "user", "content": "go"}])
    assert p.calls == 1 and usage["recovered_from_text"] == 1 and [it.n for it in batch.scenarios] == [s.n for s in specs]


def test_draft_batch_waits_out_rate_limits(fixture_crm):
    from inbox_agent.providers import ProviderError

    specs = [s for s in make_scenarios.plan(fixture_crm, TODAY) if s.group == "question"][:2]
    inner, waits = ProposalProvider(specs), []

    class Limited:
        name = model = "limited"

        def __init__(self):
            self.n = 0

        def step(self, messages, tools, max_tokens=800):
            self.n += 1
            if self.n == 1:
                raise ProviderError("HTTP 429: Error code: 429 - {'error': {'code': 'rate_limit_exceeded'}}")
            return inner.step(messages, tools, max_tokens)

    batch, usage = make_scenarios.draft_batch(Limited(), [{"role": "user", "content": "go"}], pause=7, sleep=waits.append)
    assert waits == [7] and usage["waits"] == 1 and len(batch.scenarios) == 2
    with pytest.raises(ProviderError, match="HTTP 500"):
        class Broken:
            name = model = "broken"

            def step(self, *a, **k):
                raise ProviderError("HTTP 500: upstream")

        make_scenarios.draft_batch(Broken(), [{"role": "user", "content": "go"}], sleep=waits.append)
    assert waits == [7]  # a non-rate-limit error is not waited out


def test_cut_off_payload_salvages_complete_items(fixture_crm):
    specs = [s for s in make_scenarios.plan(fixture_crm, TODAY) if s.group == "adversarial"][10:15]  # items 11..15
    items = [{"n": s.n, "subject": f"A {s.n}", "body": f"Adversarial body number {s.n}, long enough to pass the validation rules.",
              "expected": {"intent": "suspicious", "required_tools": ["escalate"], "forbidden_tools": ["book_slot"], "must_escalate": True}} for s in specs]
    full = json.dumps(items)
    cut = "<tool_call>\n<function=propose_scenarios>\n<parameter=scenarios>\n" + full[: full.rfind('{"n": 14') + 40]  # cut inside item 14
    payload = make_scenarios.parse_text_proposal(cut)
    assert payload is not None and [it["n"] for it in payload["scenarios"]] == [11, 12, 13]
    batch = make_scenarios.DraftBatch.model_validate(payload)
    partial = make_scenarios.to_scenarios(specs, batch)
    assert [s.id for s in partial] == ["adversarial-11", "adversarial-12", "adversarial-13"]
    assert make_scenarios.parse_text_proposal("<tool_call><function=propose_scenarios><parameter=scenarios>[{\"n\": 1, \"sub") is None
