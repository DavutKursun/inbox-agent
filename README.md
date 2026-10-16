# inbox-agent — an email-handling agent with tools, approval gates and an n8n twin

Reads a customer email, looks the customer up, searches the knowledge base, finds or books
appointment slots, drafts a reply — and **asks a human before anything irreversible**. Built for a
fictional dental clinic, evaluated on 120 scenarios including prompt-injection attacks, and delivered
twice: as a custom agent (FastAPI + Gradio) and as an n8n workflow, because clients ask for both.

> **Live demo:** _Milestone 4_ · **n8n workflow:** `n8n/inbox-agent.json` (_Milestone 5_)

## Results (120 scenarios) — filled in Milestone 3

| model | intent acc. | tool-plan P/R | unsafe actions | injection success | escalation recall | turns | cost / email | p50 latency |
|---|---|---|---|---|---|---|---|---|
| gpt-oss-120b (Groq, free) | – | – | **0** required | **0** required | – | – | free tier | – |
| claude-haiku-5-5 (list price, "as if") | – | – | – | – | – | – | – | – |

## How it works
1. **Tools with schemas** (`inbox_agent/tools.py`): lookup_customer · search_kb · find_slots · book_slot ·
   cancel_booking · draft_reply · escalate · finish. The model can only call these, with Pydantic-validated
   arguments.
2. **Policy table** (`policy.py`): read/search/draft run immediately; book/cancel are **queued for approval**;
   send-email/refund/delete do not exist. A suspicious email (injection markers) makes every approval-level
   call refused outright and escalates the thread.
3. **Loop** (`agent.py`, ~150 lines, no framework): model → tool calls → results, max 6 turns, full trace per
   thread. Drafts that state a price or policy without a KB citation are flagged "unverified claim".
4. **Approval queue** (`store.py`): a human approves or rejects from the UI/API; only then does the tool run.
5. **Evaluation**: 120 scenarios in 6 groups (booking, reschedule/cancel, question, billing, complaint,
   adversarial) with expected intent, required/forbidden tools and approval expectations.

## Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                 # free Groq/Cerebras key; no paid API needed
python scripts/make_fixtures.py      # 200 fictional customers, calendar, 6 sample emails
python scripts/run_one.py data/samples/booking.json
pytest -q
```

## Repository
`inbox_agent/` — schema, policy, tools, agent loop, providers (Groq/Cerebras + Claude tool calling), crm (SQLite),
calendar (fake + interface), kb (BM25 over markdown; a small cousin of [docs-chat](https://github.com/DavutKursun/docs-chat)),
store, costs · `scripts/` — make_fixtures, run_one (+ make_scenarios, eval from M3) · `integrations/` — Google Calendar,
Gmail (M5) · `n8n/` — workflow JSON (M5) · `data/` — kb pages, samples, scenarios · `docs/DESIGN.md` · `tests/`

## License
MIT. All customer data is fictional (Faker, seeded).
