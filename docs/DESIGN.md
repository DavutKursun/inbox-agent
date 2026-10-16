# Design notes — inbox-agent

## Why hand-written loop, not a framework
A client who buys an agent needs to read it. The loop is ~150 lines: prompt, tool calls, policy check,
trace. Swapping the model is one environment variable; swapping a tool is one function.

## Safety model
- Policy is a table (`policy.py`), consulted by code, not by the model.
- Approval-level tools are queued; the model is told "queued" and must tell the customer it is pending.
- Ownership checks live in the tools: a customer can only cancel their own bookings, book_slot refuses a
  customer_id that does not match the sender.
- Suspicious emails: lexical markers (cheap, transparent) flag the thread; approval tools are refused for the
  whole thread. The eval's adversarial group measures whether this plus the prompt holds up.
- The email is wrapped in `<email>` tags and described as data; the eval measures injection success.

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
