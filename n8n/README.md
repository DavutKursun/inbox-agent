# n8n workflow

`inbox-agent.json` delivers the same flow as the review UI, built in n8n: an email comes in, the agent's API decides,
and a human approves anything irreversible from a chat message.

```
Gmail Trigger (label inbox-agent, unread) ─┐
Manual test → Booking sample ──────────────┴→ Normalize email → Agent: POST /handle → Needs approval?
   yes → Approval request → Telegram configured? → yes: Ask approver (Telegram)
                                                → no:  Approval links (no Telegram configured), visible in the execution log
   no  → No approval needed
Approval link clicked (webhook) → Check decision → Agent: POST approve or reject → Reply to approver
```

## Import and configure

1. Import into n8n 2.x (checked with 2.43.8): *Workflows → Import from file*, or `n8n import:workflow --input=n8n/inbox-agent.json`.
2. Add credentials: Gmail OAuth2 on **Gmail Trigger**, a Telegram bot on **Ask approver (Telegram)**. The file ships with none.
3. Settings are constants at the top of **Normalize email** and **Check decision**. They can also come from environment
   variables when n8n allows env access in Code nodes (`N8N_BLOCK_ENV_ACCESS_IN_NODE=false`; n8n 2.x blocks it by default).

| variable | default | meaning |
|---|---|---|
| `INBOX_AGENT_URL` | `https://davutkursun-inbox-agent.hf.space` | the agent's API |
| `INBOX_AGENT_DECISION_URL` | `http://localhost:5678/webhook/inbox-agent-decision` | public URL of the **Approval link clicked** webhook |
| `INBOX_AGENT_DECISION_TOKEN` | empty | shared secret added to the links and checked by **Check decision**; set it for any public n8n |
| `INBOX_AGENT_TELEGRAM_CHAT_ID` | empty | the approver's chat; while empty, the links appear only in the execution log |

4. Activate the workflow. The Gmail Trigger polls every minute for unread mail labelled `inbox-agent`.

Slack instead of Telegram: swap **Ask approver (Telegram)** for a Slack *Send message* node with the same `message`, with
the HTML links rewritten as Slack links (`<url|Approve>`).

## What an approval link can do

A link opens the decision webhook with `action_id`, `decision` and the token. **Check decision** rejects anything else
before a request reaches the agent: a decision other than approve or reject, an id that is not `act_` plus ten hex
characters, or a wrong token. The agent then runs only actions it queued itself, and rechecks ownership and the slot
when it executes them.

## Validation (2026-10-19, headless, no credentials)

n8n 2.43.8 ran through npx on Node 24.22.0 in a temporary folder, deleted afterwards; no n8n data is in this repository.

- **Import:** `n8n import:workflow` accepted the file. Exporting it back gave the same 16 nodes, node types, versions and connections.
- **Against the live Space:** `n8n execute` ran Manual test → Booking sample → Normalize email → POST /handle. The Space
  answered 503 "no LLM provider configured", because it has no LLM key secret yet.
- **Against the same app run locally with the deterministic fake provider:** the whole intake path succeeded. /handle
  queued a `book_slot`, **Needs approval?** took the yes branch, **Approval request** built the message with Approve and
  Reject links, and **Telegram configured?** took the no branch.
- **Decision path:** replayed with curl, because a webhook needs a running, activated n8n. POST /approve/{id} on the
  local app executed the booking; the trace ends with `approved` and the queue is empty. On the live Space, GET /pending
  answered 200 with an empty list and POST /approve with an unknown id answered 404. The **Check decision** code ran
  directly in Node for approve, reject, an unknown decision, a malformed id, and a missing or correct token.
- **Not run:** the Gmail Trigger, the Telegram node and the activated webhook, which need credentials and a public
  n8n URL. The 60-second GIF of a live run is therefore a manual step.

`n8n execute` starts at the first trigger in the workflow's node list, so the manual test nodes are listed before the
Gmail Trigger. `tests/test_n8n.py` pins that order and the wiring above.
