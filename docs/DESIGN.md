# Design notes — inbox-agent

## Why hand-written loop, not a framework
A client who buys an agent needs to read it. The loop is ~150 lines: prompt, tool calls, policy check,
trace. Swapping the model is one environment variable; swapping a tool is one function.

## Safety model
- Policy is a table (`policy.py`), consulted by code, not by the model.
- Approval-level tools are checked at queue time (the customer_id / booking_id belongs to the sender; the slot is
  free, in the future and on the 30-minute grid) and only then queued; the model is told "queued" and must tell the
  customer it is pending. A failed check is an error to the model and never reaches the queue.
- Approval never raises: if the tool fails at execution time (the slot was taken meanwhile, a stale id) the action
  becomes "failed" with the error in its result, visible in the queue and the trace.
- No silent stops: a thread that hits the turn limit, a provider error or two prose-only answers is escalated with
  "agent did not finish: …" so a human always picks it up.
- Ownership checks live in the tools: lookup_customer only returns the sender's own record, a customer can only
  cancel their own bookings, book_slot refuses a customer_id that does not match the sender.
- Suspicious emails: lexical markers (cheap, transparent) flag the thread; approval tools are refused for the
  whole thread. Only explicit override phrases (English and Turkish) are markers; ordinary words such as "refund"
  are not, because there is no refund tool and a refund request is escalated by policy. The eval's adversarial
  group measures whether this plus the prompt holds up.
- The email is wrapped in `<email>` tags and described as data; the eval measures injection success.

## Fixtures
Seeded and dated: `make_fixtures.py --today 2026-10-19` writes the CRM, calendar and samples for that date and records
it in `data/fixtures_today.txt`; `run_one.py` (and later the eval) use it as their default "today", so free slots,
"next week" and the cancel sample's booking stay consistent however long after generation the agent runs. The fake
calendar derives "now" from the injected today (the whole day counts as available from 00:00), never from the wall
clock, so runs with `--today` are reproducible.

## Knowledge base
BM25 over heading-level chunks of nine markdown pages; chunk ids like `prices#price-list` are what drafts cite.
A draft that mentions money, hours or policy without a valid citation is flagged `unverified_claim`.

## Cost accounting
Measured tokens × list prices (`costs.py`); free tiers labelled "free tier"; Claude Haiku quoted "as if" so a
paying client can compare.

## Findings
_Milestone 3._

## Custom agent vs n8n
_Milestone 5: when to use which._
