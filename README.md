# inbox-agent: an email agent with tools, an approval queue and an honest evaluation

[![Open in Spaces](https://huggingface.co/datasets/huggingface/badges/resolve/main/open-in-hf-spaces-sm.svg)](https://huggingface.co/spaces/DavutKursun/inbox-agent)
[![ci](https://github.com/DavutKursun/inbox-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/DavutKursun/inbox-agent/actions/workflows/ci.yml)

**Live demo:** [huggingface.co/spaces/DavutKursun/inbox-agent](https://huggingface.co/spaces/DavutKursun/inbox-agent) (review UI at `/ui`, API at `/docs`)

![handle a booking email, approve it in the queue, watch the slot appear](docs/img/demo.gif)

It reads a customer email for a fictional dental clinic, looks the sender up, searches the clinic's knowledge base, finds
appointment slots and drafts a reply. Booking and cancelling wait in an approval queue until a human approves them, and nothing
is ever sent. The same flow also runs as an [n8n workflow](n8n/README.md) that calls the agent's API.

## Safety design
- **A policy table, not prompt wording** ([policy.py](inbox_agent/policy.py)): reading, searching and drafting run at once;
  `book_slot` and `cancel_booking` are queued for approval; sending, refunds and deletes do not exist, so any such call is refused.
- **A real approval queue**: queued actions are checked when they are queued (the booking belongs to the sender, the slot is free
  and inside opening hours) and run only on Approve; Reject leaves the calendar untouched.
- **Injection handling**: the email is data, wrapped and labelled as such; explicit override phrases in English or Turkish mark
  the thread suspicious, which refuses every approval-level call and escalates; lookups are limited to the sender; a draft can
  cite only knowledge-base chunks found in the same thread.

## Results: the deployed code (prompt 2026-10-19.2) on 120 reviewed scenarios
| model | intent | tool plan P / R | unsafe actions | injection success | escalation recall | citation rate | turns / email | cost / email | latency p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|
| claude-haiku-5-5, 120 scenarios | 0.892 | 0.992 / 0.956 | **0** | **0** | 1.000 | 1.000 | 2.9 | $0.0015 | 4.0 s / 7.6 s |
| qwen/qwen3.8-27b, the 6 samples | 6 of 6 | – | **0** | **0** | 2 of 2 | 3 of 3 | 3.0 | free tier | 46 s / 70 s |

The demo runs **qwen/qwen3.8-27b on Groq's free tier**, capped at 150 model calls and $1 per day; its 120-scenario run is pending
on that quota, and free-tier queueing explains its latency. **The three prompt versions were tuned on these same 120 scenarios,
so the Haiku numbers are optimistic; a held-out scenario set is the next step.** Run history, the ten worst scenarios with
traces and the metric definitions are in [docs/DESIGN.md](docs/DESIGN.md).

## Custom agent vs n8n: when to use which
| | custom agent (this repo) | n8n ([inbox-agent.json](n8n/inbox-agent.json)) |
|---|---|---|
| use it for | the decisions: tools, policy, approval gate, evaluation | the plumbing: Gmail trigger, approvals in Telegram or Slack, other apps |
| changes | code, tests and an eval rerun | nodes edited in the n8n UI |
| safety | enforced in code and measured | inherited, because the workflow calls the agent's API |

Use both: the agent behind an API makes the decisions and n8n wires it to the inbox and the approver, which is the split here.

## Run
```bash
pip install -r requirements-dev.txt && python scripts/make_fixtures.py --today 2026-10-19   # seeded fake CRM, calendar, samples
INBOX_FAKE_PROVIDER=1 uvicorn app:app --port 7860   # no key needed; open http://localhost:7860/ui
pytest -q                                           # 90 tests, no network
python scripts/eval.py --no-pacing --delay 8 && python scripts/eval_report.py   # the 120 scenarios, needs an LLM key
```

## Limitations and next steps
- Gmail drafts as the safe "send" are not built yet; live Google Calendar is implemented and unit-tested with a mocked client,
  but its OAuth setup is a manual step that has not been run.
- A held-out scenario set, and the qwen 120-scenario run.
- The demo's approval queue is public on purpose; a real deployment puts `/approve` and `/reject` behind authentication.
- The CRM, calendar and knowledge base are fakes, and every customer is generated data.

MIT licence. The retriever is a small cousin of [docs-chat](https://github.com/DavutKursun/docs-chat).
