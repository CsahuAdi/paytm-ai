# Build task list (from CLAUDE.md build order)

Times are from session start. Each block ends demoable and is committed.

- [ ] **0:00-0:40 Smoke tests + skeleton**: all smoke tests pass; FastAPI boots; Mongo up with indexes; seed data loads
  - [x] Repo layout, venv, requirements, .env from .env.example
  - [x] Smoke: LLM lanes (forced tool call + strict schema)
  - [x] Smoke: Sarvam (STT auto-detect, text-lid incl. romanized Hindi, mayura round trip, bulbul voices); ear test pending
  - [x] Smoke: Mongo (0.5 GB cache cap)
  - [ ] Smoke: Twilio API ok, AgentMail send/reply ok; ngrok tunnel + webhooks + sandbox join pending
  - [x] Skeleton: config, db + indexes, seed, reset_demo, FastAPI boots
- [x] **0:40-2:10 Core + router + web chat**: chat answers order status through the real tool loop on >= 2 lanes (gate 2:10)
- [x] **2:10-3:10 Rules + scenarios**: verify, refund, reship, escalation, stress test in English chat (gate 3:10)
- [ ] **3:10-3:40 Language layer**: Hindi in/out in chat; facts check; English fallback (gate 3:40)
- [x] **3:40-4:20 Approvals + takeover**: approve, reject, double-click, takeover, hand back via API (gate 4:20)
- [x] **4:20-5:20 Dashboard**: tickets, badges, timeline, queue, takeover panel, footer meters; text demo live (gate 5:20)
- [ ] **5:20-6:00 Web voice**: push-to-talk Hindi + English with spoken reply (gate 6:00): built; text fallback verified; spoken path waits on Sarvam credits
- [x] **6:00-6:30 Email**: AgentMail in/out on the same ticket as chat; go/no-go for WhatsApp (gate 6:30)
- [x] **6:30-7:00 WhatsApp**: only if the English suite is green
- [x] **7:00-7:30 Tests, stress, fixes**: full suite results and metrics
- [ ] **7:30-8:00 Rehearsals + backup video**: two rehearsals, backup video, reset_demo verified

Cut order when behind: WhatsApp, then email. Never cut: stress test, rules engine, approvals and takeover, Hindi, rehearsals.
