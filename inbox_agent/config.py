"""Settings from environment variables / .env. Every knob lives here."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
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
DATA = ROOT / "data"
KB_DIR = DATA / "kb"
CRM_DB = DATA / "crm.sqlite"
CALENDAR_JSON = DATA / "calendar.json"
SCENARIOS = DATA / "scenarios"
SAMPLES = DATA / "samples"
RUNTIME = DATA / "runtime"  # pending actions, traces (gitignored)
RESULTS = ROOT / "eval" / "results"

SEED = 7
COMPANY = "Nova Dental Clinic"
TIMEZONE = "Europe/Istanbul"
VISIT_MINUTES = 30


@dataclass(frozen=True)
class Settings:
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openai_compat"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://api.groq.com/openai/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "openai/gpt-oss-120b"))
    llm_reasoning_effort: str = field(default_factory=lambda: _env("LLM_REASONING_EFFORT", "low"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-haiku-5-5"))
    max_turns: int = field(default_factory=lambda: int(_env("MAX_TURNS", "6")))
    max_email_chars: int = field(default_factory=lambda: int(_env("MAX_EMAIL_CHARS", "2000")))
    max_daily_usd: float = field(default_factory=lambda: float(_env("MAX_DAILY_USD", "1")))
    budget_file: Path = field(default_factory=lambda: Path(_env("BUDGET_FILE", ".budget.json")))
    calendar_mode: str = field(default_factory=lambda: _env("CALENDAR_MODE", "fake"))
    gmail_mode: str = field(default_factory=lambda: _env("GMAIL_MODE", "off"))


settings = Settings()
