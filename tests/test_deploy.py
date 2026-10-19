from __future__ import annotations

import datetime as dt
import importlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import create_app
from inbox_agent.config import Settings
from inbox_agent.costs import BudgetGuard
from inbox_agent.crm import CRM

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 10, 19)


def test_space_mode_never_reads_dotenv_and_keeps_runtime_in_tmp(tmp_path, monkeypatch):
    from inbox_agent import config

    (tmp_path / ".env").write_text("INBOX_SENTINEL=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INBOX_SENTINEL", raising=False)
    monkeypatch.setenv("SPACE_ID", "DavutKursun/inbox-agent")
    monkeypatch.delenv("BUDGET_FILE", raising=False)
    monkeypatch.delenv("INBOX_RUNTIME_DIR", raising=False)
    try:
        importlib.reload(config)
        assert config.ON_SPACE and "INBOX_SENTINEL" not in os.environ  # the .env next to the app was ignored
        tmp = Path(tempfile.gettempdir()) / "inbox-agent"
        assert config.RUNTIME == tmp / "runtime" and config.Settings().budget_file == tmp / ".budget.json"
        # off the Space the same file is read
        monkeypatch.delenv("SPACE_ID")
        importlib.reload(config)
        assert not config.ON_SPACE and os.environ.get("INBOX_SENTINEL") == "from-dotenv" and config.RUNTIME == config.DATA / "runtime"
    finally:
        monkeypatch.delenv("INBOX_SENTINEL", raising=False)
        monkeypatch.delenv("SPACE_ID", raising=False)
        monkeypatch.chdir(ROOT)
        importlib.reload(config)


def test_missing_key_shows_banner_and_503_without_crashing(tmp_path, monkeypatch):
    monkeypatch.delenv("INBOX_FAKE_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai_compat")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    app = create_app(settings=Settings(), crm_path=tmp_path / "crm.sqlite", calendar_path=tmp_path / "calendar.json", store_root=tmp_path / "runtime",
                     budget=BudgetGuard(tmp_path / "b.json", 1.0, 10), today=TODAY, samples_dir=tmp_path / "samples")
    assert "No LLM key configured" in app.state.ui.banner() and "none" in app.state.ui.header()
    client = TestClient(app)
    assert client.get("/health").json()["provider"] is None
    assert client.post("/handle", json={"from": "a@example.com", "subject": "s", "body": "hello there"}).status_code == 503
    assert client.get("/ui", follow_redirects=True).status_code == 200
    assert app.state.ui.handle("a@example.com", "s", "hello there").error.startswith("No LLM provider")


def test_fixtures_regenerate_when_the_crm_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("INBOX_FAKE_PROVIDER", "1")
    data = tmp_path / "data"
    data.mkdir()
    app = create_app(crm_path=data / "crm.sqlite", calendar_path=data / "calendar.json", store_root=tmp_path / "runtime",
                     budget=BudgetGuard(tmp_path / "b.json", 1.0, 10), today=TODAY, samples_dir=data / "samples")
    assert CRM(data / "crm.sqlite").count() == 200 and (data / "calendar.json").exists() and (data / "fixtures_today.txt").read_text().strip() == "2026-10-19"
    assert len(list((data / "samples").glob("*.json"))) == 6 and len(app.state.ui.samples()) == 6
    assert TestClient(app).get("/health").json()["provider"] == "fake"


def test_dockerfile_dockerignore_and_space_readme():
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert df.startswith("# ") and "FROM python:3.11-slim" in df and "useradd -m -u 1000 user" in df and "\nUSER user\n" in df
    assert "requirements-core.txt" in df and "requirements-dev" not in df and "EXPOSE 7860" in df
    assert 'CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]' in df
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for entry in ("tests", "eval/results/traces", ".env", "data/runtime", "data/crm.sqlite", ".venv", ".git"):
        assert entry in ignore, entry
    readme = (ROOT / "space" / "README.md").read_text(encoding="utf-8")
    front = re.match(r"---\n(.*?)\n---\n", readme, re.DOTALL)
    assert front, "YAML front matter missing"
    meta = dict(line.split(":", 1) for line in front.group(1).splitlines() if ":" in line)
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    assert meta["sdk"] == "docker" and meta["app_port"] == "7860" and meta["license"] == "mit" and meta["title"] == "inbox-agent"
    assert "fictional" in readme and "Nothing is ever sent" in readme and "approval queue is real" in readme
    if shutil.which("hadolint"):
        assert subprocess.run(["hadolint", "Dockerfile"], cwd=ROOT, capture_output=True, text=True, check=False).returncode == 0
    else:
        pytest.skip("hadolint not installed: the CI docker job lints the Dockerfile")


def test_banner_and_503_name_the_key_of_the_configured_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("INBOX_FAKE_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
    app = create_app(settings=Settings(), crm_path=tmp_path / "crm.sqlite", calendar_path=tmp_path / "calendar.json", store_root=tmp_path / "runtime",
                     budget=BudgetGuard(tmp_path / "b.json", 0.20, 450), today=TODAY, samples_dir=tmp_path / "samples")
    banner = app.state.ui.banner()
    assert "ANTHROPIC_API_KEY" in banner and "LLM_API_KEY" not in banner
    r = TestClient(app).post("/handle", json={"from": "a@example.com", "subject": "s", "body": "hello there"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]
    assert "ANTHROPIC_API_KEY" in app.state.ui.handle("a@example.com", "s", "hello there").error
    assert "daily cap 450 calls / $0.2" in app.state.ui.header()
