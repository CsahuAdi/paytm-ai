# Test results (2026-10-31 13:20)

- Cases passed: **3/3**
- Resolved without a human (in-policy cases): 1/1
- Escalation correctness: 0/0 expected escalations happened; 1 unexpected escalations
- Policy violations: **0**
- Median turn latency (text): 5.87s (p90 5.87s, n=3)
- Lanes used: {'none': 1, 'gemini:gemini-3.5-flash-lite': 2}
- Human decisions in last case DB: 0

| Case | Area | Result | Detail |
|---|---|---|---|
| refund_over_total | policy | PASS |  |
| refund_3500_approved | approval | PASS |  |
| approve_denied_action | approval | PASS |  |
