| metric | claude-haiku-5-5 |
|---|---|
| scenarios | 120 booking 20, reschedule_cancel 20, question 20, billing 20, complaint 20, adversarial 20 |
| intent accuracy | 0.892 (booking 0.850, reschedule_cancel 0.950, question 1.000, billing 0.900, complaint 0.800, adversarial 0.850) |
| tool-plan precision / recall | 0.992 / 0.956 |
| unsafe actions, policy (executed without approval / non-existent tool) | 0 (0.000) |
| scenario forbidden tool called (incl. queued) | 5 (0.042) |
| injection success (adversarial) | 0 (0.000); drafts flagged for manual check: 0 |
| escalation recall (must_escalate) | 1.000 |
| escalations not expected by the scenario | 18 (0.240) |
| approval expectation match | 0.900 |
| citation rate (draft_must_cite) | 1.000 |
| forbidden_text violations | 0 |
| CRM-notes leaks, literal / key-term | 0 / 1 |
| unfinished | 2 |
| turns / tokens per email | 2.88 turns, 11534+656 tokens (0 steps estimated) |
| cost per email | $0.0015; as if claude-haiku-5-5: $0.0015 |
| latency p50 / p95 | 3968.6 ms / 7594.4 ms |
