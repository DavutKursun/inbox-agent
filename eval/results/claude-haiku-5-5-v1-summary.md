| metric | claude-haiku-5-5-v1 |
|---|---|
| scenarios | 120 booking 20, reschedule_cancel 20, question 20, billing 20, complaint 20, adversarial 20 |
| intent accuracy | 0.892 (booking 1.000, reschedule_cancel 0.950, question 1.000, billing 0.900, complaint 0.700, adversarial 0.800) |
| tool-plan precision / recall | 0.994 / 0.943 |
| unsafe actions, policy (executed without approval / non-existent tool) | 0 (0.000) |
| scenario forbidden tool called (incl. queued) | 3 (0.025) |
| injection success (adversarial) | 0 (0.000); drafts flagged for manual check: 0 |
| escalation recall (must_escalate) | 0.778 |
| escalations not expected by the scenario | 5 (0.067) |
| approval expectation match | 0.908 |
| citation rate (draft_must_cite) | 0.981 |
| forbidden_text violations | 0 |
| CRM-notes leaks, literal / key-term | 0 / 3 |
| unfinished | 0 |
| turns / tokens per email | 2.8 turns, 9507+658 tokens (0 steps estimated) |
| cost per email | $0.0013; as if claude-haiku-5-5: $0.0013 |
| latency p50 / p95 | 4470.3 ms / 7247.6 ms |
