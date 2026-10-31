# Deviations from PLAN.md

One line per change: what, why, impact on time. Also record smoke-test findings (real model names, rate limits, parameters).

## Smoke-test findings (2026-10-31, session start)

### LLM lanes
- **Model names verified via API listing**: `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite` (Gemini), `openai/gpt-oss-120b` (Groq) all exist on our keys.
- **Lane 1 `gemini-3.8-flash`**: 503 "high demand" on every try, once hung >40 s. Router failover covers it; lane order unchanged. Re-check before rehearsals.
- **Lane 2 `gemini-3.6-flash`**: forced tool call (mode ANY + `parameters_json_schema`) and strict schema (`response_json_schema`) both OK; returns thought signatures. Latency 10-16 s at default thinking, 5.7 s with `thinking_level=LOW`; `MINIMAL` gives no gain on 3.6 and is rejected by 3.8. Use `LOW` on agent calls.
- **Lane 3 Groq `openai/gpt-oss-120b`**: 5/5 forced tool calls valid, strict `json_schema` OK, 0.65 s per call. Real limits from headers: 1,000 requests/day, 8,000 tokens/min.
- **Lane 4 OpenRouter**: `nvidia/nemotron-3.5-super-vl:free` passes forced tool call (1.3 s) and strict schema with `require_parameters` (3.9 s). `xingchen-agi/xing4.0-29b-a4b:free` fails (`tool_choice` unsupported). Key: free tier, 50 free requests/day. Code rejects any non-`:free` model id.
- **Light lane `gemini-3.5-flash-lite`**: tool call 1.2 s, strict schema 2.4 s, OK.
- **Schemas**: Pydantic → `app/llm/schema.py::strict_schema` (inline `$ref`, drop titles, all required, `additionalProperties: false`) accepted by all four providers.

### Sarvam
- **text-lid**: Devanagari Hindi → `hi-IN/Deva`; English → `en-IN/Latn`; romanized Hindi is unreliable: "mera order kahan hai? …" → `pa-IN/Latn`, "bhai mera parcel abhi tak nahi aaya" → `hi-IN/Latn`.
- **mayura:v1 corrupts protected facts**: `RA-10234` → `आर.ए.-1034` (a digit dropped) in both `fully-native` and `spoken-form-in-native`; tracking `DL4471209` → `डी.एल. 4471209`. Amounts survive in `fully-native`, but `spoken-form-in-native` spells them out as words. Placeholders (`{F1}`, `#1#`) pass through unchanged.
- **mayura `source_language_code=auto`** translates romanized Hindi to clean English; `output_script=roman` gives natural romanized Hindi.
- **STT `saaras:v4` + `unknown`**: Hindi → `hi-IN` p=0.59 (0.46 s), English → `en-IN` p=1.0 (0.2 s). Accepts WebM directly (no ffmpeg needed).
- **bulbul:v4-flash** personas work: `suchitra_hi_ecomm`, `ritu_hi_customer`, `shubh_hi_ecomm` (Hindi), `simran_en_customer` (English). 1.2-2.4 s per sentence. Samples in `data/smoke/` for the ear test.
- **Spend so far**: Rs 2.07 of the Rs 5 smoke budget (estimated in `data/sarvam_spend.json`).

### Mongo
- mongod 7.0.40, `cacheSizeGB: 0.5` confirmed (`maximum bytes configured` = 536870912).

### Channels
- **Twilio**: trial account active; no messages used yet. Sandbox join and webhook pending (need ngrok domain).
- **AgentMail** (SDK 2.0.22): inbox `csahuadi@agentmail.to`. `inboxes.messages.send(inbox_id, to, subject, text)` OK. `inboxes.messages.reply(inbox_id, message_id, text=...)` stays in the same `thread_id` (reply-in-thread answered). A self-send shows as one message labelled `sent, received`. Messages carry `extracted_text` (quoted reply already stripped), `thread_id`, `in_reply_to` (null on first message). Webhooks are created with `webhooks.create(url, event_types, inbox_ids)`; none exist yet.

## Deviations
- **Protected facts are masked before translation**: replace each fact from `reply_to_customer.facts` with `{F1}`, `{F2}`, … before mayura, restore after, then run the facts check. Why: mayura drops digits from order numbers, so the check as written would send English almost every time. Impact: +10 min in the language block.
- **Romanized Indic input** (text-lid says Latin script, language not English): translate with `source_language_code=auto`; reply in the customer's language with `output_script=roman`. Why: text-lid mislabels romanized Hindi (resolves the PLAN.md open item). Impact: +5 min.
- **Agent calls on Gemini use `thinking_level=LOW`**. Why: default thinking takes 10-16 s, beyond the 10 s lane timeout. Impact: none.
- **Voice STT sends browser WebM directly to Sarvam** (no ffmpeg). Why: saaras accepts WebM. Impact: saves 5 min.
