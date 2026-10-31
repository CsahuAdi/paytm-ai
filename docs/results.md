# Test results (2026-10-31 13:28)

- Cases passed: **1/2**
- Resolved without a human (in-policy cases): 0/0
- Escalation correctness: 1/1 expected escalations happened; 1 unexpected escalations
- Policy violations: **0**
- Median turn latency (text): 2.87s (p90 2.86s, n=2)
- Lanes used: {'gemini:gemini-3.5-flash-lite': 1, 'groq:openai/gpt-oss-120b': 1}
- Human decisions in last case DB: 0

| Case | Area | Result | Detail |
|---|---|---|---|
| legal_threat_vip | escalation | PASS |  |
| primary_lane_429 | routing | FAIL | expected get_tracking executed |
