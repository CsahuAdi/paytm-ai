I'm starting an 8-hour solo hackathon build with live judging. Read `CLAUDE.md` fully, then `PLAN.md`. The plan is locked: do not redesign it, and do not ask me to confirm it. Build it.

Constraints you must respect: free tiers only (I cannot spend money), fake data only, no extra accounts or keys to multiply free limits, and my laptop is low on memory (native `mongod` with `cacheSizeGB: 0.5`, one uvicorn worker, no Docker).

Start now:

1. Create a task list from the build-order table in `CLAUDE.md`.
2. Set up the repo layout, a virtualenv, `requirements.txt` and `.env` from `.env.example`. Ask me for API keys only when a smoke test needs one, and tell me exactly which key and where to get it.
3. Run the first-hour smoke tests, in this order: LLM lanes (forced tool call + strict-schema call), Sarvam (STT auto-detect, text-lid on romanized Hindi, mayura round trip, bulbul voices), Mongo, then Twilio and AgentMail. Write the real findings (model names, rate limits, working parameters) to `docs/CHANGES.md`. If a locked choice cannot work, stop and tell me before changing it.
4. Then follow the schedule block by block. Each block must end demoable. Commit at the end of each block. Run the English test suite at the end of every block from the 3:10 mark.
5. Tell me at each acceptance gate (2:10, 3:10, 3:40, 4:20, 5:20, 6:00, 6:30). At 6:30, only start WhatsApp if the English suite is green.

Defaults while my open items are unanswered: Hindi + English, product name "ResolveAI" as a config constant. Hackathon rules about pre-built code or external services: I will confirm separately.

Begin with step 1 and report in one short message when the smoke tests are done.
