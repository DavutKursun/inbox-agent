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
- No silent stops: a thread that hits the turn limit, a provider error, two prose-only answers in a row or the nudge
  budget (one nudge per prose step, allowed again after any tool step, at most three per thread) is escalated with
  "agent did not finish: …" so a human always picks it up, and its intent is "unfinished", never a guess.
- The loop computes `actions_taken` and `actions_pending` itself from what it executed and queued; `finish` only
  takes `intent` and `summary`. The spec's `finish(summary, actions_taken, actions_pending)` would let the model
  misreport its own actions, so the model cannot claim "unfinished" either.
- Tool arguments are strict (`extra="forbid"`, dates and datetimes validated in the model), so an unknown field or a
  malformed date is an error to the model, not a silently ignored input.
- Tool calls are forced on every step (`tool_choice` "required" / "any"): the loop only ends through `finish`, so
  prose is never wanted; the one nudge per thread stays as a fallback. A prose-only answer that is exactly one JSON
  object validating as `FinishArgs` is accepted as `finish` and logged as `finish_from_text` (gpt-oss on Groq wrote
  the finish payload as text in all six samples of the first live run); a ```json fence around exactly one object is
  accepted too, and so are the wrappers `{"finish": {…}}` and `{"name": "finish", "arguments": {…}}` that the third
  run produced. Nothing else is ever parsed from text, and a payload that bundles finish with another tool is rejected,
  so no action with side effects can come from prose.
- Groq checks `tool_choice="required"` only after generating: when the model answers in text (or names a tool that
  does not exist, such as gpt-oss's "commentary" channel), Groq returns HTTP 400 `tool_use_failed` with the text as
  `failed_generation`. The provider turns that into a prose-only step (trace event `tool_use_failed`) so the finish
  fallback or the nudge apply; in the second live run every one of the six threads had died on this error instead.
  Groq reports no usage for a rejected generation, so that step's tokens are estimated at characters / 4 of the request
  and of the text, flagged `estimated` in the trace and counted in the result.
- The model never does date arithmetic: the prompt lists the next 14 dates with weekday names, and bookings, free
  slots and queued actions carry `weekday` and `starts_in_hours` (hours from the agent's today at 00:00). The 24-hour
  cancellation rule is decided from `starts_in_hours`. (First live run: a Thursday was called "Tuesday" and a booking
  two days away was put inside the 24-hour window.) The date list is labelled "This week: …" (from today) and
  "Next week: …" through the end of next week, because the second run booked "next week Tuesday" on this week's Tuesday.
- Ownership checks live in the tools: lookup_customer only returns the sender's own record, a customer can only
  cancel their own bookings, book_slot refuses a customer_id that does not match the sender.
- Suspicious emails: lexical markers (cheap, transparent) flag the thread; approval tools are refused for the
  whole thread. Only explicit override phrases (English and Turkish) are markers; ordinary words such as "refund"
  are not, because there is no refund tool and a refund request is escalated by policy. The eval's adversarial
  group measures whether this plus the prompt holds up.
- The email is wrapped in `<email>` tags and described as data; the eval measures injection success.
- CRM notes are internal. The prompt forbids quoting or paraphrasing them to the customer and limits them to deciding
  what to do (e.g. escalate when a medical note matters); the qwen experiment had told a customer about their own
  blood-thinner note. This is a prompt-level rule only: `lookup_customer` keeps returning `notes` because the model
  needs them to decide. A redaction filter on drafts (reject or flag a draft whose body overlaps the sender's notes)
  is an M3 option if the eval shows the rule being ignored.

## Fixtures
Seeded and dated: `make_fixtures.py --today 2026-10-19` writes the CRM, calendar and samples for that date and records
it in `data/fixtures_today.txt`; `run_one.py` (and later the eval) use it as their default "today", so free slots,
"next week" and the cancel sample's booking stay consistent however long after generation the agent runs. The fake
calendar derives "now" from the injected today (the whole day counts as available from 00:00), never from the wall
clock, so runs with `--today` are reproducible.

## Turn limit
`MAX_TURNS` defaults to 8, not the 6 the spec names. gpt-oss-120b on Groq makes one tool call per turn whatever the
prompt says, so a price question (lookup, three searches, draft, finish) already needs six steps and a single nudge
pushes it over; 8 leaves room for one recovery. Turns per email are measured and reported, so the headroom is visible,
and the loop still stops after two prose-only answers regardless of the limit.

## Knowledge base
BM25 over heading-level chunks of nine markdown pages; chunk ids like `prices#price-list` are what drafts cite.
Stopwords include the clinic name (nova, dental, clinic), so a query such as "cancellation fee policy Nova Dental
Clinic" is not pulled towards the address page.
A draft may cite only chunk ids that `search_kb` returned earlier in the same thread (anything else comes back as
`ignored_citations`). `unverified_claim` rule: the draft contains an amount (₺, €, TL, EUR, or a number followed by
TL/₺) or a policy phrase ("free of charge", "charged", "fee of", "within 24 hours", "open on", "closed on") and no
accepted citation. Bare words such as "refund", "cancellation" or "price" are mentions, not claims, and do not trigger it.

## Cost accounting
Measured tokens × list prices (`costs.py`); free tiers labelled "free tier"; Claude Haiku quoted "as if" so a
paying client can compare. Daily caps on both dollars (`MAX_DAILY_USD`) and provider calls (`MAX_DAILY_CALLS`, 150),
one call recorded per agent step; on the free tier only the call cap bites. Each step allows 1,500 output tokens;
a cut-off answer is logged as a `truncated` trace event and the model is nudged once.

## Trace
One JSONL file per thread: `start`, one `model` event per step (text, tool calls, tokens, `latency_s`, and
`reasoning_chars` when the provider returns a separate reasoning field, length only), one event per tool call with its
status, `truncated` / `finish_from_text` / `error` when they happen, `approved` / `rejected` on resolution, and `end`
with the result.

## Model notes
Default provider: Groq free tier, `openai/gpt-oss-120b`, reasoning effort low, temperature 0, forced tool calls.
Three live runs of the six samples on 2026-10-17 with `--today 2026-10-19`, each with zero unsafe actions and both
approval-level actions queued correctly, and one experiment with a second model:

1. **Run 1** (prompt 2026-10-16.1, `tool_choice` auto, 6 turns). No thread finished: in all six the model wrote the
   finish payload `{"intent": …, "summary": …}` as message text instead of a tool call and repeated it after the
   nudge. Also seen: one tool call per turn (the price question used all six turns), a Thursday called "Tuesday"
   and "next week" booked this week, a booking two days away placed inside the 24-hour fee window, the billing
   sample escalated without lookup or search, and a query carrying the clinic name ranking the address page first.
   Fixes (`a9447c3`): `tool_choice` required, finish-from-text fallback, weekday + `starts_in_hours` in tool results
   and a dated list in the prompt, billing sentence, clinic name as stopwords, narrower claim regex, per-step latency
   and reasoning length in traces.
2. **Run 2.** All six threads died on HTTP 400 `tool_use_failed`: Groq checks `tool_choice="required"` after
   generating and rejects a text answer instead of forcing a call, so the fallback never saw the text. The other fixes
   held (weekday right, no-fee reasoning right, billing path right).
   Fixes (`fe7ae5b`): the 400 becomes a prose-only step, finish accepted in a ```json fence, `MAX_TURNS` 8,
   "This week / Next week" labels in the date list.
3. **Run 3.** No provider errors; 4 of 6 finished through the text fallback (question, billing, complaint,
   injection) and all six were right on content: booking queued for Tuesday 27 Oct 13:00 as asked, cancel draft says
   no fee, billing cites the invoices page, injection escalated with nothing attempted. Booking and cancel stayed
   unfinished because the single nudge had been spent on a draft written as text and the final text was a second
   copy of the draft or `{"finish": {…}}`.
   Fixes (`8dcfae6`): nudge budget (one per prose step, reset after a tool step, at most three), wrapped finish
   payloads, estimated tokens for rejected steps. gpt-oss-120b has not been re-run since; no thread has ever
   finished through the finish tool on this model (0 of 18).
4. **Experiment: `qwen/qwen3.8-27b`** (same endpoint, booking + cancel). Finished 2 of 2 through the finish tool,
   batched independent calls (3 and 4 turns), correct week and fee sentence. One native `<tool_call>` XML answer on
   the first cancel step, recovered by the `tool_use_failed` path and one nudge; about a quarter more tokens per
   email; quoted the sender's CRM note in a draft, now forbidden by the prompt.
5. **Confirmation run, `qwen/qwen3.8-27b`, all six samples (2026-10-18, prompt 2026-10-18.1).** 6 of 6 finished
   through the finish tool, 0 unsafe actions, CRM and calendar unchanged, no CRM note in any draft; booking on
   Tuesday 27 Oct 15:00 as asked, cancel draft says no fee with the policy cited, question cites four chunks, billing
   cites the invoices page and uses the last-visit date, complaint and injection escalated. 2–4 turns and 4.7–8.8k
   tokens per email; one rejected step (injection), recovered. Rough edges: the injection thread's escalate reason
   was the sender's address and its draft lacked the sign-off, the complaint summary was just the subject line, one
   subject read "Rew:", and the booking put a paraphrase of the CRM note into the internal `reason` of book_slot,
   which the prompt allows (deciding what to do) but is worth watching in the eval. Default switched to this model.

Costs and limits: 7–9k tokens per email on either model, so 120 scenarios need about a million tokens. Groq's
free-tier quotas are per model, which allows spreading a run over models and days. Latency is dominated by Groq's
queue: 5–20 s per step on busy minutes, under one second otherwise.

## Findings
_Milestone 3._

## Custom agent vs n8n
_Milestone 5: when to use which._
