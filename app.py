"""inbox-agent API (Milestone 4): FastAPI endpoints around the agent and its approval queue, with the Gradio review UI
mounted at /ui.

Run:            uvicorn app:app --port 7860
Without a key:  INBOX_FAKE_PROVIDER=1 uvicorn app:app --port 7860   (a deterministic stand-in drives the loop)

Endpoints: POST /handle, GET /pending, POST /approve/{action_id}, POST /reject/{action_id}, GET /trace/{thread_id},
GET /health. One KB, one Store, one CRM and calendar on data/, one Agent built from settings at startup; requests are
logged without message bodies. No CORS. Request and response models are the ones in inbox_agent/schema.py.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi import Path as PathParam

from inbox_agent.agent import Agent
from inbox_agent.calendar import FakeCalendar
from inbox_agent.config import CALENDAR_JSON, CRM_DB, ROOT, RUNTIME, SAMPLES, Settings, fixtures_today
from inbox_agent.config import settings as default_settings
from inbox_agent.costs import BudgetGuard
from inbox_agent.crm import CRM
from inbox_agent.kb import KnowledgeBase
from inbox_agent.providers import ProviderError, get_provider
from inbox_agent.schema import AgentResult, Email, PendingAction
from inbox_agent.store import Store
from inbox_agent.tools import Context

log = logging.getLogger("inbox_agent.app")
THREAD_ID = r"^[A-Za-z0-9_-]{1,64}$"
ACTION_ID = r"^[A-Za-z0-9_-]{1,64}$"


def create_app(*, settings: Settings = default_settings, provider=None, crm_path: Path | str = CRM_DB, calendar_path: Path | str = CALENDAR_JSON,
               store_root: Path | str = RUNTIME, budget: BudgetGuard | None = None, today=None, kb: KnowledgeBase | None = None, ui: bool = True,
               samples_dir: Path | str = SAMPLES) -> FastAPI:
    """Build the app with its singletons and, by default, the review UI at /ui. Tests pass a scripted provider, throwaway
    fixture paths and a temp budget file."""
    kb = kb or KnowledgeBase()
    store = Store(store_root)
    today = today or fixtures_today()
    if not Path(crm_path).exists():  # a fresh container or Space: rebuild the seeded fixtures (same customers as the scenarios)
        log.warning("no CRM at %s: generating the seeded fixtures for %s", crm_path, today)
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts.make_fixtures import build

        build(today=today, n=200, crm_path=crm_path, calendar_path=calendar_path, samples_dir=samples_dir, today_file=Path(crm_path).parent / "fixtures_today.txt")
    if provider is None:
        try:
            provider = get_provider(settings)
        except ProviderError as exc:
            log.warning("no LLM provider: %s (POST /handle will answer 503; approvals still work)", exc)
    budget = budget or BudgetGuard(settings.budget_file, settings.max_daily_usd, settings.max_daily_calls)

    def ctx_factory(thread_id: str, customer_email: str) -> Context:
        return Context(crm=CRM(crm_path), calendar=FakeCalendar(calendar_path, today=today), kb=kb, store=store, thread_id=thread_id, customer_email=customer_email)

    agent = Agent(provider, ctx_factory, budget=budget, today=today)  # resolve() works without a provider
    app = FastAPI(title="inbox-agent", version="0.4.0", description="Email agent with tools and an approval queue (fictional Nova Dental Clinic).")
    app.state.agent, app.state.store, app.state.budget, app.state.provider, app.state.settings = agent, store, budget, provider, settings

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        log.info("%s %s -> %d in %.0f ms", request.method, request.url.path, response.status_code, (time.perf_counter() - t0) * 1000)
        return response

    @app.post("/handle", response_model=AgentResult, responses={413: {"description": "email body too long"}, 429: {"description": "daily budget exhausted"},
                                                                 503: {"description": "no LLM provider configured"}})
    def handle(email: Email) -> AgentResult:
        if provider is None:
            raise HTTPException(503, "no LLM provider configured: set LLM_API_KEY or ANTHROPIC_API_KEY (or INBOX_FAKE_PROVIDER=1 for a local check)")
        if len(email.body) > settings.max_email_chars:
            raise HTTPException(413, f"email body longer than {settings.max_email_chars} characters")
        if not budget.can_spend():
            raise HTTPException(429, "daily budget exhausted; try again tomorrow")
        try:
            res = agent.handle(email)
        except RuntimeError as exc:  # the budget guard, raised inside the loop
            raise HTTPException(429, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(413, str(exc)) from exc
        log.info("handle thread=%s intent=%s turns=%d stopped=%s pending=%d latency_ms=%.0f", res.thread_id, res.intent, res.turns, res.stopped_reason,
                 len(res.actions_pending), res.latency_ms)
        return res

    @app.get("/pending", response_model=list[PendingAction])
    def pending() -> list[PendingAction]:
        return store.pending()

    def _resolve(action_id: str, approve: bool) -> PendingAction:
        try:
            action = agent.resolve(action_id, approve=approve)
        except KeyError as exc:
            raise HTTPException(404, f"unknown action {action_id}") from exc
        log.info("%s action=%s tool=%s status=%s", "approve" if approve else "reject", action_id, action.tool, action.status)
        return action

    @app.post("/approve/{action_id}", response_model=PendingAction, responses={404: {"description": "unknown action"}})
    def approve(action_id: str = PathParam(pattern=ACTION_ID)) -> PendingAction:
        return _resolve(action_id, True)

    @app.post("/reject/{action_id}", response_model=PendingAction, responses={404: {"description": "unknown action"}})
    def reject(action_id: str = PathParam(pattern=ACTION_ID)) -> PendingAction:
        return _resolve(action_id, False)

    @app.get("/trace/{thread_id}", response_model=list[dict], responses={404: {"description": "unknown thread"}})
    def trace(thread_id: str = PathParam(pattern=THREAD_ID)) -> list[dict]:
        if not store.trace_path(thread_id).exists():
            raise HTTPException(404, f"no trace for thread {thread_id}")
        return store.read_trace(thread_id)

    @app.get("/health")
    def health() -> dict:
        spend = budget.read()
        return {"status": "ok", "model": provider.model if provider else None, "provider": provider.name if provider else None,
                "pending_count": len(store.pending()), "budget": {"calls_today": spend.calls, "usd_today": round(spend.usd, 4)}}

    if ui:
        import gradio as gr

        from inbox_agent.ui import ReviewUI

        review = ReviewUI(agent, store, budget, provider, settings, calendar_path, today, samples_dir)
        app.state.ui = review
        gr.mount_gradio_app(app, review.build(), path="/ui")
    return app


app = create_app()
