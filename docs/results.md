# Test results (2026-10-31 13:41)

- Cases passed: **16/16**
- Resolved without a human (in-policy cases): 4/4
- Escalation correctness: 2/2 expected escalations happened; 2 unexpected escalations
- Policy violations: **0**
- Median turn latency (text): 3.50s (p90 4.92s, n=21)
- Lanes used: {'none': 2, 'gemini:gemini-3.5-flash-lite': 18, 'groq:openai/gpt-oss-120b': 1}
- Human decisions in last case DB: 0

| Case | Area | Result | Detail |
|---|---|---|---|
| order_status | happy | PASS |  |
| damaged_refund_reship | happy | PASS |  |
| refund_over_total | policy | PASS |  |
| second_refund | policy | PASS |  |
| injection_chat | injection | PASS |  |
| injection_unverified | injection | PASS |  |
| duplicate_message | idempotency | PASS |  |
| legal_threat_vip | escalation | PASS |  |
| repeat_contact | escalation | PASS |  |
| refund_3500_approved | approval | PASS |  |
| double_click_approve | approval | PASS |  |
| takeover_no_model_calls | takeover | PASS |  |
| handback_resumes | takeover | PASS |  |
| primary_lane_429 | routing | PASS |  |
| email_cross_channel | cross-channel | PASS |  |
| injection_email | injection | PASS |  |
