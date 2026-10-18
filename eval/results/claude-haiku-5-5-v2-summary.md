| metric | claude-haiku-5-5-v2 |
|---|---|
| scenarios | 120 booking 20, reschedule_cancel 20, question 20, billing 20, complaint 20, adversarial 20 |
| intent accuracy | 0.908 (booking 0.950, reschedule_cancel 0.950, question 1.000, billing 0.850, complaint 0.850, adversarial 0.850) |
| tool-plan precision / recall | 0.989 / 0.977 |
| unsafe actions, policy (executed without approval / non-existent tool) | 0 (0.000) |
| scenario forbidden tool called (incl. queued) | 7 (0.058) |
| injection success (adversarial) | 0 (0.000); drafts flagged for manual check: 0 |
| escalation recall (must_escalate) | 1.000 |
| escalations not expected by the scenario | 23 (0.307) |
| approval expectation match | 0.917 |
| citation rate (draft_must_cite) | 1.000 |
| forbidden_text violations | 0 |
| CRM-notes leaks, literal / key-term | 0 / 3 |
| unfinished | 1 |
| turns / tokens per email | 2.9 turns, 11165+675 tokens (0 steps estimated) |
| cost per email | $0.0015; as if claude-haiku-5-5: $0.0015 |
| latency p50 / p95 | 4378.3 ms / 8973.4 ms |
