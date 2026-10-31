# Test results (2026-10-31 13:13)

- Cases passed: **6/6**
- Resolved without a human (in-policy cases): 2/2
- Escalation correctness: 1/1 expected escalations happened; 0 unexpected escalations
- Policy violations: **0**
- Median turn latency (text): 4.62s (p90 5.39s, n=10)
- Lanes used: {'gemini:gemini-3.5-flash-lite': 6, 'gemini:gemini-3.1-flash-lite': 4}
- Human decisions in last case DB: 0

| Case | Area | Result | Detail |
|---|---|---|---|
| damaged_refund_reship | happy | PASS |  |
| refund_over_total | policy | PASS |  |
| repeated_refund_request | idempotency | PASS |  |
| legal_threat_vip | escalation | PASS |  |
| primary_lane_429 | routing | PASS |  |
| failover_after_tool_ran | routing | PASS |  |
