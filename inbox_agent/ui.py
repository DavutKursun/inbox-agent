"""The Gradio review UI (Milestone 4, step 2), mounted on the FastAPI app at /ui.

Three columns: Inbox (the six sample emails or a pasted one, Handle, the last ten threads), Result (intent, summary,
escalation, the draft with citation chips and the unverified badge, a thought-free trace viewer, tokens / cost /
latency), Approval queue (pending actions with Approve / Reject per row through the same resolve path as the API, and
the next 14 days of bookings so a slot is seen to appear only after approval). The callbacks are plain methods, so
tests call them directly; ``build()`` wires them into a ``gr.Blocks``.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

import gradio as gr
from pydantic import ValidationError

from .agent import Agent
from .calendar import FakeCalendar, weekday_name
from .config import COMPANY, SAMPLES
from .costs import BudgetGuard, format_cost
from .schema import AgentResult, Email, PendingAction
from .store import Store

PASTE = "paste your own"
SAMPLE_ORDER = ("booking", "cancel", "question", "billing", "complaint", "injection")  # the demo order; others follow alphabetically
REPO = "https://github.com/DavutKursun/inbox-agent"
TRACE_HEADERS = ["turn", "tool", "args", "status", "result"]
RECENT_HEADERS = ["thread", "intent", "escalated", "pending"]
QUEUE_HEADERS = ["action_id", "tool", "args", "customer", "created"]
CALENDAR_HEADERS = ["day", "start", "min", "title"]


def _short(value, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class UIResult:
    """What one Handle produces for the Result column; ``error`` is set instead when the input was refused."""

    result: AgentResult | None = None
    error: str = ""
    summary_md: str = ""
    draft_subject: str = ""
    draft_body: str = ""
    citations_md: str = ""
    trace_rows: list[list] | None = None
    stats_md: str = ""


class ReviewUI:
    def __init__(self, agent: Agent, store: Store, budget: BudgetGuard, provider, settings, calendar_path: Path | str, today: dt.date,
                 samples_dir: Path | str = SAMPLES) -> None:
        self.agent, self.store, self.budget, self.provider, self.settings = agent, store, budget, provider, settings
        self.calendar_path, self.today, self.samples_dir = Path(calendar_path), today, Path(samples_dir)
        self._tick = 0

    # --- data for the panels ---------------------------------------------------------------
    def samples(self) -> dict[str, dict]:
        out = {}
        for p in sorted(self.samples_dir.glob("*.json"), key=lambda p: (SAMPLE_ORDER.index(p.stem) if p.stem in SAMPLE_ORDER else len(SAMPLE_ORDER), p.stem)):
            try:
                out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue
        return out

    def pick_sample(self, name: str) -> tuple[str, str, str]:
        s = self.samples().get(name)
        return (s["from"], s["subject"], s["body"]) if s else ("", "", "")

    def banner(self) -> str:
        """A warning for the top of the page when no LLM is configured (the queue and traces still work)."""
        if self.provider is not None:
            return ""
        return ("⚠️ **No LLM key configured.** Handle answers with an error and `POST /handle` with 503; approvals, traces and "
                "the calendar still work. Set `LLM_API_KEY` (Groq) or `ANTHROPIC_API_KEY` with `LLM_PROVIDER=anthropic` as a "
                "secret, or start with `INBOX_FAKE_PROVIDER=1` for a local check.")

    def header(self) -> str:
        spend = self.budget.read()
        model = self.provider.model if self.provider else "none (set a key or INBOX_FAKE_PROVIDER=1)"
        provider = self.provider.name if self.provider else "none"
        return (f"**{COMPANY} — inbox agent** · model `{model}` · provider `{provider}` · today {spend.calls} calls, "
                f"{format_cost(model, spend.usd)} · daily cap {self.budget.max_daily_calls} calls / ${self.budget.max_daily_usd:g}")

    def recent(self, n: int = 10) -> list[list]:
        rows = []
        for p in sorted((self.store.root / "traces").glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[: n * 2]:
            events = self.store.read_trace(p.stem)
            end = next((e for e in reversed(events) if e.get("event") == "end"), None)
            if end:
                r = end["result"]
                rows.append([p.stem, r.get("intent", ""), "yes" if r.get("escalated") else "no", len(r.get("actions_pending", []))])
            if len(rows) == n:
                break
        return rows

    def pending_actions(self) -> list[PendingAction]:
        return self.store.pending()

    def pending_rows(self) -> list[list]:
        rows = []
        for a in self.pending_actions():
            path = self.store.root / "pending" / f"{a.action_id}.json"
            created = dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if path.exists() else ""
            rows.append([a.action_id, a.tool, _short(a.args, 80), a.customer_email, created])
        return rows

    def calendar_rows(self, days: int = 14) -> list[list]:
        cal = FakeCalendar(self.calendar_path, today=self.today)
        end = (self.today + dt.timedelta(days=days)).isoformat()
        rows = []
        for b in sorted(cal.busy, key=lambda b: b["start"]):
            if self.today.isoformat() <= b["start"][:10] <= end:
                start = dt.datetime.fromisoformat(b["start"])
                rows.append([weekday_name(start), b["start"], b.get("duration_min", 30), b.get("title", "")])
        return rows

    def trace_rows(self, thread_id: str) -> list[list]:
        rows = []
        for e in self.store.read_trace(thread_id):
            if "call" in e:
                rows.append([e["turn"], e["call"]["name"], _short(e["call"]["args"], 80), e["status"], _short(e.get("result"), 120)])
            elif e.get("event") in ("finish_from_text", "tool_use_failed", "truncated", "error"):
                rows.append([e.get("turn", ""), e["event"], "", "", _short({k: v for k, v in e.items() if k not in ("event", "turn")}, 120)])
        return rows

    # --- actions -----------------------------------------------------------------------------
    def handle(self, frm: str, subject: str, body: str) -> UIResult:
        try:
            email = Email(**{"from": (frm or "").strip(), "subject": subject or "", "body": body or ""})
        except ValidationError as exc:
            return UIResult(error="Invalid email: " + "; ".join(e["msg"] for e in exc.errors()))
        if self.provider is None:
            return UIResult(error="No LLM provider configured: set a key in .env or start with INBOX_FAKE_PROVIDER=1.")
        if len(email.body) > self.settings.max_email_chars:
            return UIResult(error=f"Email body longer than {self.settings.max_email_chars} characters.")
        if not self.budget.can_spend():
            return UIResult(error="Daily budget exhausted; try again tomorrow.")
        try:
            res = self.agent.handle(email)
        except RuntimeError as exc:
            return UIResult(error=str(exc))
        self._tick += 1
        d = res.draft_reply
        chips = " ".join(f"`{c}`" for c in (d.citations if d else [])) or "_no citations_"
        badge = " · **⚠ unverified claim**" if d and d.unverified_claim else ""
        summary = (f"**Intent:** {res.intent} · **stopped:** {res.stopped_reason} · **thread** `{res.thread_id}`\n\n{res.summary}"
                   + (f"\n\n**Escalated:** {res.escalation_reason}" if res.escalated else "")
                   + (f"\n\n**Pending approval:** {', '.join(a.tool for a in res.actions_pending)}" if res.actions_pending else ""))
        stats = (f"{res.turns} turns · {res.input_tokens}+{res.output_tokens} tokens · {format_cost(res.model, res.cost_usd)} · "
                 f"{res.latency_ms / 1000:.1f} s · prompt {self._prompt_version()}")
        return UIResult(result=res, summary_md=summary, draft_subject=d.subject if d else "", draft_body=d.body if d else "",
                        citations_md=f"**Citations:** {chips}{badge}", trace_rows=self.trace_rows(res.thread_id), stats_md=stats)

    def resolve(self, action_id: str, approve: bool) -> str:
        """Approve or reject through the same path as the API; returns a one-line message for the queue panel."""
        try:
            action = self.agent.resolve(action_id, approve=approve)
        except KeyError:
            return f"Unknown action `{action_id}`."
        self._tick += 1
        return f"`{action.action_id}` {action.tool} → **{action.status}**" + (f" · {_short(action.result, 160)}" if action.result is not None else "")

    @staticmethod
    def _prompt_version() -> str:
        from .agent import PROMPT_VERSION

        return PROMPT_VERSION

    # --- the Blocks ---------------------------------------------------------------------------
    def build(self) -> gr.Blocks:
        samples = self.samples()
        names = list(samples) + [PASTE]
        first = samples[names[0]] if samples else {"from": "", "subject": "", "body": ""}

        def handle_event(frm, subject, body):
            r = self.handle(frm, subject, body)
            if r.error:
                return (f"**Error:** {r.error}", gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), self.recent(), self._tick, self.header())
            return (r.summary_md, r.draft_subject, r.draft_body, r.citations_md, r.trace_rows, r.stats_md, self.recent(), self._tick, self.header())

        def resolve_event(action_id: str, approve: bool):
            return self.resolve(action_id, approve), self._tick, self.header()

        with gr.Blocks(title=f"{COMPANY} — inbox agent") as demo:
            refresh = gr.State(0)
            header = gr.Markdown(self.header())
            gr.Markdown(self.banner(), visible=bool(self.banner()))
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("### Inbox")
                    sample = gr.Dropdown(choices=names, value=names[0], label="Sample email")
                    frm = gr.Textbox(label="From", value=first["from"])
                    subject = gr.Textbox(label="Subject", value=first["subject"])
                    body = gr.Textbox(label="Body", value=first["body"], lines=8)
                    handle_btn = gr.Button("Handle", variant="primary")
                    recent = gr.Dataframe(value=self.recent(), headers=RECENT_HEADERS, interactive=False, label="Last 10 threads")
                with gr.Column(scale=2):
                    gr.Markdown("### Result")
                    summary = gr.Markdown("_Handle an email to see the result._")
                    d_subject = gr.Textbox(label="Draft subject", interactive=False)
                    d_body = gr.Textbox(label="Draft body (never sent)", lines=10, interactive=False)
                    cites = gr.Markdown("")
                    trace = gr.Dataframe(value=[], headers=TRACE_HEADERS, interactive=False, label="Trace (tool calls only, no model text)", wrap=True)
                    stats = gr.Markdown("")
                with gr.Column(scale=1):
                    gr.Markdown("### Approval queue")
                    queue_msg = gr.Markdown("")

                    @gr.render(inputs=[refresh], triggers=[refresh.change, demo.load])
                    def queue(_tick):
                        rows = self.pending_rows()
                        if not rows:
                            gr.Markdown("_No pending actions._")
                        for action_id, tool, args, customer, created in rows:
                            with gr.Row():
                                gr.Markdown(f"`{action_id}` **{tool}** {args}  \n{customer} · {created}")
                                ok = gr.Button("Approve", variant="primary", size="sm")
                                no = gr.Button("Reject", size="sm")
                                ok.click(lambda aid=action_id: resolve_event(aid, True), outputs=[queue_msg, refresh, header])
                                no.click(lambda aid=action_id: resolve_event(aid, False), outputs=[queue_msg, refresh, header])

                    with gr.Accordion("Show calendar (next 14 days)", open=False):
                        calendar = gr.Dataframe(value=self.calendar_rows(), headers=CALENDAR_HEADERS, interactive=False)
            gr.Markdown(f"_{COMPANY} is fictional and every customer is generated data. Drafts are never sent; bookings and cancellations "
                        f"run only after a human approves them here or through the API. Source: [{REPO}]({REPO})._")
            sample.change(self.pick_sample, inputs=[sample], outputs=[frm, subject, body])
            handle_btn.click(handle_event, inputs=[frm, subject, body], outputs=[summary, d_subject, d_body, cites, trace, stats, recent, refresh, header])
            refresh.change(lambda: self.calendar_rows(), outputs=[calendar])
            demo.load(lambda: (self.header(), self.recent(), self.calendar_rows()), outputs=[header, recent, calendar])
        return demo
