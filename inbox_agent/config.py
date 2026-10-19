"""Settings from environment variables / .env. Every knob lives here."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ON_SPACE = bool(os.environ.get("SPACE_ID"))  # Hugging Face sets SPACE_ID on a Space: settings come from its secrets, never a file
TMP = Path(tempfile.gettempdir()) / "inbox-agent"  # ephemeral runtime on a Space


def _load_dotenv(path: Path = Path(".env")) -> None:
    if ON_SPACE or not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.split("#", 1)[0].strip().strip('"').strip("'"))


_load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ["INBOX_DATA_DIR"]) if os.environ.get("INBOX_DATA_DIR") else ROOT / "data"  # override for demos/tests
KB_DIR = DATA / "kb"
CRM_DB = DATA / "crm.sqlite"
CALENDAR_JSON = DATA / "calendar.json"
SCENARIOS = DATA / "scenarios"
SAMPLES = DATA / "samples"
RUNTIME = Path(os.environ["INBOX_RUNTIME_DIR"]) if os.environ.get("INBOX_RUNTIME_DIR") else (TMP / "runtime" if ON_SPACE else DATA / "runtime")  # gitignored
RESULTS = ROOT / "eval" / "results"
FIXTURES_TODAY = DATA / "fixtures_today.txt"  # the date the fixtures were built for (scripts/make_fixtures.py --today)


def fixtures_today(path: Path = FIXTURES_TODAY) -> date:
    """The "today" the fixtures were generated for, so slots and samples line up however late the agent runs.
    Falls back to the real date when the file is missing or unreadable."""
    try:
        return date.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return date.today()


SEED = 7
COMPANY = "Nova Dental Clinic"
TIMEZONE = "Europe/Istanbul"
VISIT_MINUTES = 30


@dataclass(frozen=True)
class Settings:
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openai_compat"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://api.groq.com/openai/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "qwen/qwen3.8-27b"))  # M3 step 0: 6/6 finishes via tool
    llm_reasoning_effort: str = field(default_factory=lambda: _env("LLM_REASONING_EFFORT", "low"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-haiku-5-5"))
    max_turns: int = field(default_factory=lambda: int(_env("MAX_TURNS", "8")))  # 8, not the spec's 6: see DESIGN.md "Turn limit"
    max_email_chars: int = field(default_factory=lambda: int(_env("MAX_EMAIL_CHARS", "2000")))
    max_daily_usd: float = field(default_factory=lambda: float(_env("MAX_DAILY_USD", "1")))
    max_daily_calls: int = field(default_factory=lambda: int(_env("MAX_DAILY_CALLS", "150")))
    budget_file: Path = field(default_factory=lambda: Path(_env("BUDGET_FILE", str(TMP / ".budget.json") if ON_SPACE else ".budget.json")))
    calendar_mode: str = field(default_factory=lambda: _env("CALENDAR_MODE", "fake"))
    gmail_mode: str = field(default_factory=lambda: _env("GMAIL_MODE", "off"))


settings = Settings()
