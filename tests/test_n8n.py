"""The exported n8n workflow: structure, wiring and the defaults it ships with (no n8n needed)."""

from __future__ import annotations

import json
import re
from pathlib import Path

WF = json.loads((Path(__file__).resolve().parents[1] / "n8n" / "inbox-agent.json").read_text(encoding="utf-8"))
NODES = {n["name"]: n for n in WF["nodes"]}


def targets(name: str, branch: int = 0) -> list[str]:
    return [c["node"] for c in WF["connections"][name]["main"][branch]]


def test_workflow_wiring_matches_the_design():
    assert WF["active"] is False and WF["settings"]["executionOrder"] == "v1" and len(WF["id"]) <= 21
    assert targets("Gmail Trigger") == targets("Booking sample") == ["Normalize email"] and targets("Manual test") == ["Booking sample"]
    assert targets("Normalize email") == ["Agent: POST /handle"] and targets("Agent: POST /handle") == ["Needs approval?"]
    assert targets("Needs approval?", 0) == ["Approval request"] and targets("Needs approval?", 1) == ["No approval needed"]
    assert targets("Telegram configured?", 0) == ["Ask approver (Telegram)"] and targets("Telegram configured?", 1) == ["Approval links (no Telegram configured)"]
    assert targets("Approval link clicked") == ["Check decision"] and targets("Check decision") == ["Agent: POST approve or reject"]
    assert targets("Agent: POST approve or reject") == ["Reply to approver"]
    every_target = {c["node"] for out in WF["connections"].values() for branch in out["main"] for c in branch}
    assert every_target | set(WF["connections"]) <= set(NODES)  # no dangling connection


def test_cli_start_and_node_versions():
    triggers = [n["name"] for n in WF["nodes"] if n["type"].endswith(("Trigger", "trigger")) or n["type"] == "n8n-nodes-base.webhook"]
    assert triggers[0] == "Manual test"  # `n8n execute` starts at the first trigger in the list (checked against n8n 2.43.8)
    versions = {n["type"]: n["typeVersion"] for n in WF["nodes"]}
    assert versions["n8n-nodes-base.httpRequest"] == 4.2 and versions["n8n-nodes-base.if"] == 2 and versions["n8n-nodes-base.code"] == 2
    assert versions["n8n-nodes-base.telegram"] == 1.2 and versions["n8n-nodes-base.webhook"] == 2 and versions["n8n-nodes-base.gmailTrigger"] == 1.2


def test_defaults_point_at_the_live_space_and_ship_no_secrets():
    handle = NODES["Agent: POST /handle"]["parameters"]
    assert handle["method"] == "POST" and handle["url"] == "={{ $json.agent_url }}/handle" and handle["specifyBody"] == "json"
    assert "https://davutkursun-inbox-agent.hf.space" in NODES["Normalize email"]["parameters"]["jsCode"]
    assert "https://davutkursun-inbox-agent.hf.space" in NODES["Check decision"]["parameters"]["jsCode"]
    assert all("credentials" not in n for n in WF["nodes"])  # credentials are added after import
    assert NODES["Ask approver (Telegram)"]["parameters"]["additionalFields"]["appendAttribution"] is False
    text = json.dumps(WF)
    assert not re.search(r"gsk_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{30,}|\d{8,10}:AA[A-Za-z0-9_-]{30,}", text)


def test_booking_sample_and_decision_rules():
    sample = json.loads((Path(__file__).resolve().parents[1] / "data" / "samples" / "booking.json").read_text(encoding="utf-8"))
    assert json.dumps(sample, ensure_ascii=False) in NODES["Booking sample"]["parameters"]["jsCode"]
    check = NODES["Check decision"]["parameters"]["jsCode"]
    assert "['approve', 'reject'].includes(q.decision)" in check and "^act_[a-f0-9]{10}$" in check and "q.token !== TOKEN" in check
    assert NODES["Approval link clicked"]["parameters"]["responseMode"] == "responseNode"
