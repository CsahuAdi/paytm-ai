# ResolveAI: build brief for Claude Code

**Amendment 1 (domain packs, tool grants, admin role): see `../amendments.md`; it wins over this file and `PLAN.md` where they conflict.**
**Amendment 2 (Paytm-channel kirana pack, retention loop): see `../docs/AMENDMENT_02_paytm_kirana.md`; it wins over Amendment 1 where they conflict.**

Read this file first, then `PLAN.md` (the locked plan; it wins on any detail). You are building a customer-service AI teammate for an e-commerce/D2C store in **one 8-hour solo session with live judging**. The goal is a working, demoable system, not a polished codebase. "ResolveAI" is a placeholder name.

Hackathon theme: *"Build AI teammates that don't just respond; they get the job done."* The system must resolve tickets end to end, act across systems under rules the model cannot override, and hand off to humans with context.

## Hard constraints

1. **Free to use. Spend no money.** Free tiers only. Never add a paid service, a paid plan or a credit card step. If something needs payment, stop and tell the user.
2. **Fake data only.** Free-tier LLM content may be used for training by the provider. Seed data and tests use invented people, phones, emails and orders.
3. **No extra accounts, projects or keys to multiply free limits.** One key per provider (Gemini, Groq, OpenRouter). The key loader accepts a comma-separated list and rotates round-robin, but do not create more.
4. **Laptop is low on memory.** Local `mongod` with `cacheSizeGB: 0.5`, installed natively (no Docker). One uvicorn worker. No heavy dev tooling, no vector DB, no Redis.
5. **Time is the budget.** 8 hours, zero slack. Follow the schedule below and the cut order. Prefer the simplest thing that satisfies the plan.

## Architecture rules (do not violate)

- **English internally.** Sarvam translates at the edges. Every message stores `text` (original) and `text_en`. The agent, the rules, the summaries and the humans all use English. Never send English text to Sarvam translate or LID.
- **One core.** Every channel adapter normalizes to an `Inbound` dataclass (`channel, external_id, sender, text, modality, language, thread_hint`) and calls `core.handle()`. Adapters stay thin (~30 minutes each).
- **Structured outputs only.** The agent acts only through tool calls with Pydantic-validated arguments. Force tool choice (Gemini mode ANY; `tool_choice: required` on Groq/OpenRouter). A turn ends when the model calls `reply_to_customer`. Invalid args go back once as a tool error; a second failure moves the turn to the next LLM lane. Non-tool calls (conversation summary, handoff summary) use JSON-schema structured output with flat schemas, all fields required, `additionalProperties: false`; check length limits in code. **No code parses free text.**
- **Rules engine is plain Python (`policy.py`) outside the model.** The model proposes, code decides: allow / confirm / approve / escalate / deny. Deny rules are hard: approval lifts only `refund_limit` and `vip_review`, and the executor re-checks deny rules at run time.
- **Customer text is untrusted data.** Wrap the new message in `<customer_message>` delimiters. The engine reads the proposed action plus the database, never the model's reasoning.
- **Identity.** Only the `verify_identity(order_number, factor)` tool can verify (email on order for web/email, last 4 phone digits for voice). A WhatsApp number or email sender matching the customer record auto-verifies. Three failures escalate. Unverified sessions get context blocks 1, 2, 6 only, and data tools are denied.
- **No transactions** (standalone mongod). Use single-document atomicity, unique indexes and the idempotency pattern: insert the `actions` row as `executing` with its idempotency key, run side effects, mark `executed`. Approval uses atomic `find_one_and_update` on `status` (compare-and-set).
- **MongoDB:** PyMongo `AsyncMongoClient` (not Motor), created once in the FastAPI lifespan.
- **Context is rebuilt from the DB every turn.** Keep no per-conversation state in memory. Tiered history: last 10 messages raw; older folded into `tickets.summary` (+ `summary_upto`, `summary_version`) when 20 messages sit after `summary_upto` (summarize the oldest 10 plus the existing summary, recency-weighted). Summarizer runs in the background on `gemini-3.5-flash-lite`. Raw logs always kept. Target ~2k tokens per packet.
- **Per-ticket `asyncio.Lock`**, single worker, in-process pub/sub over SSE for the live dashboard (no change streams).
- **One ngrok tunnel** for all webhooks. Twilio: ack immediately, process in background, reply via REST, validate the signature against the public tunnel URL. AgentMail (Svix): verify with the `svix` package and the raw body, dedupe on payload `event_id`.
- **Failure behaviour:** nothing fails silently. Translation/LID/402 failure means the agent reads the original text, replies in English and flags `meta.translation_failed`. All LLM lanes failing means tell the customer a human will follow up, and escalate. STT/TTS failure means a text reply with an on-screen note.

## LLM router

Lanes in order: Gemini `gemini-3.8-flash` -> Gemini `gemini-3.6-flash` -> Groq `openai/gpt-oss-120b` -> an OpenRouter `:free` model that lists tool support. Light calls (summaries): `gemini-3.5-flash-lite` -> Groq -> OpenRouter. A lane fails on 429, 5xx, 10 s timeout, or invalid structure after one repair. Cooldown = `Retry-After` or 30 s; a lane at its daily limit is off until midnight Pacific (Gemini) or 24 h (others). Mid-turn failover restarts the turn on the next lane (idempotency keys prevent duplicate actions). All lanes get the same prompt and tool schemas. Per-lane call counters and cooldown status go in the dashboard footer, next to the Sarvam spend meter. Use the `openai` SDK for Groq and OpenRouter (OpenAI-compatible) and `google-genai` for Gemini. Model names and free limits move fast: **verify in the provider console/docs during the smoke tests**, and record the real values in `docs/CHANGES.md`.

## Tools (10)

`verify_identity`, `get_order`, `get_tracking`, `issue_refund`, `create_reship`, `cancel_order`, `send_notification`, `confirm_action`, `escalate`, `reply_to_customer` (fields: `reply_en`, `facts[{kind,value}]`, `sentiment` calm|frustrated|angry|distressed, `confidence` low|medium|high, `escalation_reason` none|legal_threat|fraud_signal|distress|customer_requested_human). The business systems (orders, refunds, notifications) are mocks backed by MongoDB.

## Policy tiers (locked values)

- **Allow:** read tools, notifications.
- **Confirm (customer says yes first):** refund up to **Rs 2,000**, reship, cancel an unshipped order.
- **Approve (human):** refund above Rs 2,000; **any VIP refund**.
- **Escalate:** legal threat, fraud signal, distress, low confidence, third contact in 30 days (`repeat_contacts_30d` computed in code), three failed identity checks, customer asks for a human.
- **Deny (hard):** any data/action tool before identity verified; refund above the order total; second refund on the same order.

## Data model (7 collections)

`customers`, `orders` (items + shipment embedded), `refunds`, `identities`, `tickets`, `messages`, `actions`. Fields and indexes are in `PLAN.md` ("Data model"). One open ticket per customer, shared across channels; an unverified temporary ticket merges into the customer's open ticket on verification.

## Language layer (Sarvam)

STT `saaras:v4` with `language_code=unknown`; LID `POST /text-lid`; translation `mayura:v1` (mode `formal`; `output_script=fully-native` for text, `spoken-form-in-native` for voice, fall back to `fully-native` if it sounds wrong); TTS `bulbul:v4-flash` with one persona per demo language (Malayalam and Odia are not on v4-flash: reply as text; fallback model `bulbul:v3`, speaker `shubh`). 11 languages; anything else gets an English reply. Protected-facts check after translation (order numbers, amounts, dates, tracking numbers); on a miss, send the English reply. Reply caps: 900 chars text, 250 voice; split long inbound text at sentence boundaries (1,000-char API limit). Rate limit 60 req/min per endpoint. **Credit budget Rs 100:** smoke 5, dev 30, tests 15, rehearsals 15, demo 25, reserve 10. Implement the spend meter; at Rs 10 left, stop all non-demo Sarvam calls. Automated tests run in English (zero Sarvam cost).

## Human in the loop

Two dashboard paths, one level, no login (name dropdown recorded as `decided_by` / `author_name`): (1) approve/reject one held action from the "Needs attention" queue (VIP first, then oldest); (2) take over a ticket (`mode=human`: `core.handle` stores the message and publishes an event but makes no model call), reply in English (Sarvam translates), resolve or hand back (`mode=ai`, agent resumes with `human_note`). Not built: SLA timers, reminders, multi-level approvals, login, human-run tool calls. Log approval rate, rejection rate, takeover count, time to decision.

## Repo layout (suggested)

```
app/
  main.py            FastAPI app + lifespan (Mongo client, router, pub/sub)
  core.py            handle(): dedupe, identity, ticket, lock, packet, loop, deliver
  context.py         context packet builder + tiered summary
  policy.py          rules engine (pure functions, unit-tested)
  tools.py           Pydantic tool schemas + executors (idempotent)
  db.py              collections, indexes, helpers
  events.py          in-process pub/sub + SSE
  llm/               router.py, gemini.py, openai_compat.py (Groq, OpenRouter), keys.py
  lang/              sarvam.py (stt, lid, translate, tts), facts_check.py
  channels/          web.py, voice.py, email_agentmail.py, whatsapp_twilio.py
  api/               dashboard + approvals + takeover endpoints
static/              chat.html, voice.html, dashboard.html (plain HTML + JS, no build step)
scripts/             smoke_*.py, seed.py, reset_demo.py, simulate_channels.py, run_suite.py
tests/               ~35 scripted cases + policy unit tests
fixtures/            twilio_*.json, agentmail_*.json, hindi phrases
docs/CHANGES.md      log every deviation from PLAN.md, with the reason
.env.example
```

## Build order and acceptance checks

Times are from session start. Each block ends demoable. Run the English suite at the end of every block from 3:10.

| Block | Time | Done when |
|---|---|---|
| Smoke tests + skeleton | 0:00-0:40 | All smoke tests below pass; FastAPI boots; Mongo up with indexes; seed data loads |
| Core + router + web chat | 0:40-2:10 | Chat answers order status through the real tool loop on at least two LLM lanes |
| Rules + scenarios | 2:10-3:10 | Verify, refund, reship, escalation and the stress test (over-order-total refund, prompt injection) work in English chat |
| Language layer | 3:10-3:40 | Hindi in and out in chat; facts check works; failure falls back to English |
| Approvals + takeover | 3:40-4:20 | Approve, reject, double-click, takeover, hand back all work via the API |
| Dashboard | 4:20-5:20 | Ticket list, channel badges, action timeline, approval queue, takeover panel, footer meters. **Text demo live** |
| Web voice | 5:20-6:00 | Push-to-talk (browser recording, 30 s max) works in Hindi and English, with spoken reply |
| Email | 6:00-6:30 | AgentMail in/out on the same ticket as chat. **Go/no-go for WhatsApp at 6:30** |
| WhatsApp | 6:30-7:00 | Only if the English suite is green; else spend on fixes. Twilio sandbox, ~100 messages total |
| Tests, stress, fixes | 7:00-7:30 | Full suite results and metrics in hand |
| Rehearsals + backup video | 7:30-8:00 | Two full rehearsals; backup video recorded; `reset_demo` verified |

**Cut order when behind:** WhatsApp first, then email. **Never cut:** stress test, rules engine, approvals and takeover, Hindi language layer, the two rehearsals.

## Smoke tests (first 40 minutes)

1. One forced tool call and one strict-schema call on each LLM lane (Gemini, Groq, OpenRouter). Pick the OpenRouter `:free` model that lists tool support; check `gpt-oss-120b` tool-call reliability; read real rate limits from consoles.
2. Sarvam STT with auto-detect on Hindi and English clips; `/text-lid` on Devanagari Hindi, English and **romanized Hindi** (behaviour undocumented); `mayura:v1` round trip keeping an order number and amount intact; `bulbul:v4-flash` persona IDs per demo language and `spoken-form-in-native` by ear.
3. Twilio sandbox join and webhook through ngrok; AgentMail send and receive (reply-in-thread call, `in_reply_to`, attachments); tunnel URL survives a restart.
4. Mongo starts with the 0.5 GB cache cap.

Record every finding in `docs/CHANGES.md`. Stop and ask the user if a smoke test shows a locked choice cannot work.

## Demo (about 6 minutes, 8 beats)

Dashboard on seeded data -> Hindi voice order status -> damaged item with refund + reship (chat) -> cross-channel "any update?" -> angry VIP escalation, takeover and reply -> Rs 3,500 refund held then approved -> Rs 50,000 refund + prompt-injection message blocked and logged -> show test numbers. `scripts/reset_demo.py` restores seed data in one command. Seed data must contain: a refund under Rs 2,000, one near Rs 3,500, a VIP customer, a Hindi speaker, a second-language speaker, orders well under Rs 50,000, and a shipped and an unshipped order.

## Tests and metrics

About 35 scripted cases across: happy paths, identity, policy, approval, takeover, injection, idempotency, cross-channel, language, failure, LLM routing (see `PLAN.md`). `scripts/simulate_channels.py` posts Twilio-shaped and AgentMail-shaped fixtures so tests never spend WhatsApp messages. Report: resolution rate without a human, escalation correctness, approval/rejection rates, policy violations (target 0), median latency (text and voice). **Nothing goes on a slide before it is measured.**

## Working rules for you

- Start building immediately after reading `PLAN.md`; do not re-plan. Ask the user only when blocked by a decision that is theirs (credentials, a locked choice that cannot work, spending money).
- Keep a task list; commit at the end of each block with a short message.
- Write plain, readable Python 3.11+, type hints on public functions, no frameworks beyond those listed. Front end is plain HTML/JS, no build step.
- Unit-test `policy.py` thoroughly (it is the product's core claim). Everything else is covered by the scripted suite.
- Never put secrets in the repo. Read keys from `.env`.
- If you deviate from the plan, add one line to `docs/CHANGES.md`: what, why, impact on time.
- Stack: `fastapi uvicorn pymongo(async) pydantic google-genai openai sarvamai twilio svix agentmail httpx python-dotenv pytest`. Check each SDK's current docs rather than trusting memory.

## Open items (ask the user at kickoff)

Second demo language the user can verify (one of the 11 TTS languages); product/team name; hackathon start time and judging format. Do not block on these: default to Hindi + English, keep "ResolveAI" as a config constant.
