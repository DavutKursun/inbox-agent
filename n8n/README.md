# n8n twin (Milestone 5)
Gmail trigger → HTTP Request to `POST /handle` → IF `actions_pending` not empty → Slack/Telegram message with
Approve / Reject → HTTP Request to `POST /approve/{id}` or `/reject/{id}`. Exported as `inbox-agent.json`.
