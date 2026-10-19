| metric | claude-haiku-5-5-v4 |
|---|---|
| scenarios | 120 booking 20, reschedule_cancel 20, question 20, billing 20, complaint 20, adversarial 20 |
| intent accuracy | 0.842 (booking 0.900, reschedule_cancel 0.850, question 1.000, billing 0.800, complaint 0.800, adversarial 0.700) |
| tool-plan precision / recall | 0.986 / 0.965 |
| unsafe actions, policy (executed without approval / non-existent tool) | 0 (0.000) |
| scenario forbidden tool called (incl. queued) | 8 (0.067) |
| injection success (adversarial) | 0 (0.000); drafts flagged for manual check: 1 |
| escalation recall (must_escalate) | 1.000 |
| escalations not expected by the scenario | 19 (0.253) |
| approval expectation match | 0.900 |
| citation rate (draft_must_cite) | 0.943 |
| forbidden_text violations | 0 |
| CRM-notes leaks, literal / key-term | 0 / 1 |
| unfinished | 1 |
| turns / tokens per email | 2.85 turns, 11141+641 tokens (0 steps estimated) |
| cost per email | $0.0014; as if claude-haiku-5-5: $0.0014 |
| latency p50 / p95 | 4315.7 ms / 8497.1 ms |
