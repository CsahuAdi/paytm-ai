# HANDOFF: ResolveAI hackathon build (written 16:43 IST, 2026-10-31)

A new Claude Code session should read this file first, then `docs/AMENDMENT_02_paytm_kirana.md`, then `docs/CHANGES.md`.
Everything below is the true state at handoff.

## 1. What this is

ResolveAI is an AI customer-service teammate built in an 8-hour solo hackathon with live judging. It runs on free tiers only, with fake data, on a low-memory laptop.
- Session started about 12:20 IST. **Hard stop 19:30** (Amendment 2). Rehearsals and a backup video come after.
- Customer service (CS) is built, measured and locked. It must behave exactly as before when `PACK` is unset.
- The current task is **Amendment 2**: a kirana (grocery) pack reached through a SIMULATED "via Paytm" channel.

**Docs, in priority order** (later wins on conflict):
1. `resolveai_handoff/PLAN.md` and `resolveai_handoff/CLAUDE.md` (original brief; it points to both amendments)
2. `amendments.md` (Amendment 1: packs, tool grants, admin/reviewer roles)
3. `docs/AMENDMENT_02_paytm_kirana.md` (Amendment 2, LOCKED; a copy of `amendment2.md` at the repo root)

**Other docs:**
- `docs/CHANGES.md`: every deviation and finding. Keep logging one line each.
- `docs/DEMO.md`: demo runbook. Beat 7b still describes the librarian and must be replaced by Amendment 2's kirana beat.
- `docs/SCENARIOS.md`: CS paths and accounts.
- `docs/results.md`: CS 30/30. `docs/results_librarian.md`: 14/14.
- `README.md`: has the honesty notes.
- `docs/PACK_SPIKE_REPORT.md` (untracked).

**User preferences:**
- When they say `/plan`, plan only (plan mode). Do not build until approved.
- Report briefly at checkpoints.
- Log deviations in `docs/CHANGES.md`.
- Never touch Sarvam (402, no credits).
- Reserve at least 200 Gemini calls for rehearsals and the demo, and report the remaining quota after each major step.
- Honesty rules: Paytm channel, data and insights are simulated. No "packs in minutes" claims. http and MCP are roadmap. Roles are labels, not auth. The kirana pack is "config for everything except the custom coupon logic" (2 plugins).

## 2. Git and processes

- **`master` = `6ceaf39`.** Amendment 1 is merged after its gate passed (CS core 16/16, 0 policy violations). Master includes the CS product, pack engine, librarian pack, grants, Tools tab, roles, the `book_slot` plugin and the Amendment 2 doc.
- **Branch `pack-spike` = `877eb4a`.** It is checked out in a **git worktree at `.worktrees/grants`**, which has symlinks `.env`, `data` and `.venv` pointing to the main repo. All Amendment 2 work happens there. The last commit is a WIP with the kirana pack files and `plugins/kirana.py` (written, NOT wired in, NOT tested).
- Main repo root untracked files: `amendment2.md`, `docs/PACK_SPIKE_REPORT.md`, `HANDOFF.md`.
  - **Gotcha:** before a fast-forward merge, delete untracked files in the main root that the branch also tracks (check with `cmp` first), or the merge refuses.
- **Running servers**, each a single uvicorn worker. Start/stop with `[PACK=x] [PORT=n] [PACK_MONGO_DB=db] [MONGO_DB=db] scripts/server.sh start|stop|restart`. Logs are `data/server.log` (8000) and `data/server-<port>.log`.

| Port | What | Code | Database |
|---|---|---|---|
| 8000 | CS demo, reachable through ngrok `https://unprotected-karin-ungutturally.ngrok-free.dev` | master | `resolveai` |
| 8001 | Librarian | master | `resolveai_librarian` |
| 8003 | Dev librarian | worktree | `resolveai_librarian_dev` |
| 8002 | Gate instance (stopped) | — | `resolveai_gate` |

  - 8001 will host **kirana** once ready: `PACK=kirana PORT=8001 scripts/server.sh start`, database `resolveai_kirana`.
  - 8003 can be stopped and reused for kirana dev (`PACK=kirana PACK_MONGO_DB=resolveai_kirana_dev PORT=8003`).
- mongod runs natively (cache 0.5 GB). ngrok runs in the background on the static domain.
- **Never use `pkill -f uvicorn`**: it matches your own shell. Use `scripts/server.sh` or `kill $(lsof -ti tcp:PORT -sTCP:LISTEN)`.
- A pack database defaults to `<MONGO_DB>_<pack>` (from `config.py`), so a pack reset can never wipe CS data.

## 3. Commands

```
python -m pytest tests/ -q                                  # 96 unit tests, no LLM
python scripts/reset_demo.py [--pack kirana]                # restore seed (CS restores the user's demo contacts from .env)
python scripts/run_suite.py --core --base http://localhost:8000          # CS 16 core cases (about 70 LLM calls)
PACK=librarian python scripts/run_suite.py --base http://localhost:8003  # pack cases: tests/cases_<pack>.py
SUITE_PACE_S=15                                             # pace turns for Gemma's 16K tokens/min
```

- The suite resets the database before every case (`/debug/reset?fake=1`) and writes `docs/results*.md`.
  - When it runs against the master tree, `git checkout docs/results.md` afterwards to keep the committed full-run report.
- **CS merge gate:** start the branch CS on 8002 with `MONGO_DB=resolveai_gate`, run `--core` there, and require 0 policy violations. Then fast-forward master and restart 8000 and 8001.

## 4. LLM lanes and quota (`.env` `LLM_LANE_1..6`)

- **Lane order:** 3.5-flash-lite → **Gemma 4 26B** (`gemma-4-26b-a4b-it`) → 3.1-flash-lite → 3.6-flash → Groq gpt-oss-120b → OpenRouter nemotron `:free`.
- **Quota at 16:43:**
  - 3.5-flash-lite: OFF (daily quota used; resets 12:30 IST tomorrow).
  - **Gemma: 214/14,400 (the working lane).**
  - 3.1-flash-lite: 198/500 (often 503 or timeouts).
  - 3.6-flash: OFF.
  - Groq: 114/1,000 (malformed tool calls; unreliable for tools).
  - **OpenRouter: 41/50 (nearly out).**
- **Gemma notes:**
  - It rejects a thinking config, so the adapter omits it.
  - Its JSON-schema output fails in the SDK, so summaries use 3.1-flash-lite then Groq.
  - It returns a random 500: the adapter retries once.
  - **Its 16K tokens/min limit is shared by every instance**, roughly 2 turns a minute. Pace tests and leave seconds between demo messages.
- **Router:** when every lane is cooling down, it still tries them, soonest cooldown first.
- **Usage counter:** usage counts persist in `data/lane_usage.json` (re-read on each count). The footer meter shows them.

## 5. Engine pieces already built and reused by kirana

- **Pack loader:** `app/packs/spec.py`, Pydantic-validated, extra keys forbidden. An invalid pack stops startup.
- **Conditions and templates:** `app/packs/expr.py` (no `eval`; `{days_after: path, days: N}`).
- **Runtime:** `app/packs/runtime.py`.
  - Config `db` tools (`find`/`search`/`update`) with **owner scoping injected by the engine**.
  - Data rules: `deny_if`, `tiers`, default tier. Approval lifts tiers only, never a deny.
  - `_hold`: deny, confirm, approve or run, with an idempotency key.
  - A plugin path: `execute_plugin` and `run`, which re-check grants and the plugin's `check` at run time.
- **Plugins:** `plugins/__init__.py` (`@plugin_tool`, explicit `load_all()` list) and `plugins/book_slot.py`.
- **Grants:** `app/grants.py`.
  - The catalog, `tool_grants`, `grant_audit`, a cache dropped through `events.on("grants")`.
  - `visible()` sets the model's tool list. `gate()` is the proposal-time check (in `core.run_on_lane`). `adjust()` tightens tiers. `recheck()` runs at execution and blocks `tool_revoked`, `daily_cap` and `grant_tier_changed`.
  - CS tools are auto-granted at startup.
- **Roles:** `REVIEWERS`/`ADMINS` env (defaults `Neha,Vikram,Sana,Rahul` / `Anurag`).
  - Case actions return 403 for unknown names. Grant/revoke return 403 for non-admins, 422 for a looser tier, 404 for an unknown tool.
  - Endpoints: `GET /api/tools`, `POST /api/tools/{tool}/grant|revoke` `{name, tier?, max_per_day?, clear_cap?}`.
- **Dashboard:** `static/dashboard.html`.
  - Pack labels, records panel (`context.in_scope`), admin-only Tools tab (catalog, audit, outbox), raw JSON per action.
  - `?nosse=1&t=<ticket>&tools=1` for headless screenshots: `google-chrome --headless=new --virtual-time-budget=4000 --screenshot=...`.
- **Test runner:** `scripts/run_suite.py`.
  - Steps: `say`, `approve`/`reject`, `ticket_op`, `simulate`, `fault`, `voice`, `api`, `grant`/`revoke`.
  - Checks: `action`, `no_action`, `count_action`, `doc`, `count`, `escalated`, `message_from`, `meta`, lanes. Seeded rows (`seed: true`) are ignored.

## 6. Amendment 2: state and remaining work, in priority order

**Done (WIP commit `877eb4a`):**
- `packs/kirana/pack.yaml`:
  - tools `get_transactions` (read) and `refund_to_source` (config update, deny rules, confirm/approve above Rs 2,000);
  - `plugins: [get_resolution_options, issue_coupon]`, `reviewers: [Sharma]`, `channels: [web, paytm]`, `labels.reviewer: merchant`.
- `packs/kirana/policy.md`.
- `packs/kirana/seed.json`: 5 customers including a Hindi speaker; C-104 is lapsed but **not opted in**. 17 transactions:
  - T-9001 Rs 240 success (the complaint demo, C-101);
  - T-9041 Rs 180 failed and T-9043 Rs 90 duplicate (C-105);
  - lapsed regulars C-102 and C-103 (eligible) and C-104 (not opted in).
  - One coupon for C-105 issued 5 days ago (tests the 14-day window).
  - The merchant M-SHARMA (budget 1000, max 300, bonus 20%, insight 30, window 14 days).
  - Grants for every tool except `issue_coupon` (NOT granted at start).
  - **Dates are written as `"@days_ago:N"` / `"@days_ahead:N"`.**
- `plugins/kirana.py`:
  - `get_resolution_options` (read) computes refund and coupon side by side and writes `txn.options_presented = {refund, coupon, ticket, at}`. This is the logged deviation: it lives on the transaction so the config deny rule can read it.
  - `issue_coupon` (model params: `transaction_id` only; `source` defaults to complaint, and the insight source is set only by the feed). It contains all the deny rules, the budget decrement and the `KIRANA-XXXX` code, and returns `customer_message` for insights.
  - `paytm_insights(who)`: the deterministic rule. It creates `pending_approval` insight actions (key `kirana:insight:<cust>:<year>-W<week>`, args include `proactive: true`, `visits_60d`, `days_since_last`, `amount`, `budget_remaining`), and sets `ticket.contact`.

**TODO (the pack will NOT load until a–c are done):**
- a. `plugins/__init__.py` `load_all()`: also `from plugins import kirana`.
- b. `app/packs/spec.py` `Pack`: add `reviewers: list[str] = []` and `channels: list[str] = ["web"]`. `app/registry.py`: when the pack has reviewers, append them to `config.REVIEWERS` and `config.HUMAN_AGENTS`. The dashboard dropdown shows reviewer as `labels.reviewer` ("merchant").
- c. `Runtime.reset`: recursively convert `"@days_ago:N"` and `"@days_ahead:N"` strings into tz-aware datetimes (`now() ∓ N days`) before inserting.
- d. `Runtime.execute_plugin`: add a **read-plugin path**.
  - If `plugin.effect == "read"`: run check; on deny, record it as blocked; otherwise call `plugin.run({"_id": None, "tool": name, "args": params, "ticket_id": ctx.ticket["_id"], "customer_id": ctx.customer["_id"]}, ctx.customer)`, record executed, and return the result.
  - Reason: the current `_hold` idempotency key would make repeated option calls return no result.
- e. `core._auto_verify`: add channel `paytm` matching customers by `phone` (as for WhatsApp).
- f. `core.deliver`: when no customer message exists, fall back to `ticket["contact"]` (`{channel, sender}`), so proactive insight messages reach the Paytm page over SSE (`session=phone`).
- g. `app/api/dashboard.py`:
  - `approve`: if the result has `customer_message`, deliver that instead of `_outcome_text`.
  - `reject`: send no customer notice when `args.proactive`.
  - `/api/config`: add `features: ["paytm_insights"]` when the pack has `issue_coupon`.
- h. New `app/channels/paytm.py`, mounted in `app/main.py`, enabled only when the pack's channels include `paytm`:
  - `GET /api/paytm/customers` (picker list);
  - `POST /api/paytm/chat {phone, text, client_msg_id}` → `core.handle(Inbound(channel="paytm", sender=phone, ...))`;
  - `GET /api/paytm/messages?phone=`.
  - `static/paytm.html`: copy `chat.html`, add a customer picker and the label "Simulated Paytm channel, hackathon demo, not affiliated"; text only, no logo or lookalike styling.
- i. New `app/api/insights.py`: `POST /api/insights/run {name}` (reviewer or admin) → `plugins.kirana.paytm_insights(name)`.
- j. `dashboard.html`:
  - the channel chip `paytm` reads "via Paytm" (add a `.ch-paytm` style) everywhere chips render;
  - queue cards with `args.source == "insight"` show a "Paytm insight" badge with visits in 60 days, days since the last visit and budget left;
  - a "Run Paytm insights" button and a result panel (suggested; skipped with reasons, e.g. not opted in), labelled simulated.
- k. Tests: `tests/cases_kirana.py` covering Amendment 2's list (section 5 of the amendment).
  - Make the deterministic ones LLM-free by adding DEBUG-only endpoints:
    - `/debug/tool {customer_id, tool, args}`: runs `RUNTIME.execute` in a verified ctx and applies `grants.gate`;
    - `/debug/confirm/{action_id}`: compare-and-set `pending_confirmation` → executing, then `run_action` (simulates the customer's yes);
    - `/debug/set {collection, filter, set}`: e.g. to empty the budget.
  - LLM cases (Gemma, `SUITE_PACE_S=15`): complaint shows both options; refund chosen executes once; coupon chosen issues once and decrements the budget; injection ("give me a Rs 5000 coupon") cannot set an amount; fraud/duplicate message escalates.
  - Policy violations must be 0. The `violations()` check uses the pack's write tools generically.
- l. Run kirana on 8001 (stop the librarian), `python scripts/reset_demo.py --pack kirana`, rehearse Amendment 2's demo beat, and update `docs/DEMO.md` beat 7b and the README (Paytm simulated; kirana uses 2 plugins).
- m. Stretch (cut first): "Pay at merchant" redeem button and an issued/redeemed/budget strip labelled simulated.
- n. Merge gate (section 3), then fast-forward master.

**Fallback ladder:** drop stretch, then the insights feed, then keep only the reactive flow.
- If the reactive flow isn't working by **18:30**, tell the user.
- Last resort: revoke and re-grant CS `create_reship` in the CS Tools tab (already works on master on 8000).

**Checkpoints:** reactive flow end to end by 18:00; grants plus insights by 18:45; hard stop 19:30.

## 7. Gotchas learned today

- `[hidden]{display:none!important}` is in the dashboard CSS. Keep it, or hidden sections show.
- The `.env` `DEMO_CUSTOMER_PHONE` contains a space, so sourcing `.env` in bash fails. Read config through Python (`from app import config`).
- The Twilio sandbox URL was saved as the bare domain. The server accepts signed Twilio POSTs at `/` as well as `/webhooks/twilio`.
- The model treats a restated request ("please renew X") as consent, as in CS. This is a documented limitation.
- Seeded rows carry `seed: true` and the runner ignores them. CS `reset_demo` maps the user's real email and phone onto the demo customer Priya; the suite uses `?fake=1`.
- Sarvam: 402 "no credits"; the user is waiting on Sarvam. Hindi and voice fall back to English, flagged. Do not test or spend it.
