# HANDOFF: ResolveAI hackathon build (rewritten after the merge, 2026-10-31)

A new Claude Code session should read this file first, then `docs/CHANGES.md` (every deviation and finding) and `docs/DEMO.md` (the runbook).

## 1. Where things stand

ResolveAI is an AI customer-service teammate built solo in one 8-hour hackathon session (started about 12:20 IST) on **free tiers only with invented data**. **Everything planned is built, tested and merged.** Amendment 2's hard stop was 19:30; the remaining work is rehearsals, the backup video and the slide deck, which need the user.

- **`master` = the merge commit `617250a`** (branch `pack-spike` merged; the branch is kept but there is no worktree any more). Work directly on `master` from now on.
- Three servers run from the main repo, one uvicorn worker each, each pack with its own database in one shared local `mongod` (0.5 GB cache cap):

| Port | What | Database | Start | Reset |
|---|---|---|---|---|
| 8000 | **Customer service** (the main product; the ngrok tunnel `https://unprotected-karin-ungutturally.ngrok-free.dev` points here) | `resolveai` | `scripts/server.sh start` | `python scripts/reset_demo.py` |
| 8001 | **Kirana** merchant desk; simulated "via Paytm" chat at `/paytm` | `resolveai_kirana` | `PACK=kirana PORT=8001 scripts/server.sh start` | `python scripts/reset_demo.py --pack kirana` |
| 8005 | **Librarian** (zero-Python pack) | `resolveai_librarian` | `PACK=librarian PORT=8005 scripts/server.sh start` | `python scripts/reset_demo.py --pack librarian` |

- `scripts/server.sh stop|restart` also take `PORT=`/`PACK=`. Logs: `data/server.log` (8000) and `data/server-<port>.log`. **Never use `pkill -f uvicorn`** (it matches your own shell); use `server.sh` or `kill $(lsof -ti tcp:PORT -sTCP:LISTEN)`.
- **Servers read `.env` once at start.** If `.env` changes (keys, lanes, contacts), restart the servers.

## 2. What the product is

- **Core claim:** the model proposes through structured tool calls; plain Python (`app/policy.py`, plus the pack rules) decides allow / confirm / approve / escalate / deny; humans approve or take over from the dashboard. Deny rules are hard (approval lifts only the limit and VIP rules; the executor re-checks at run time).
- **Customer service** (`app/core.py`, `app/tools.py`, `app/policy.py`, `app/context.py`): 10 tools over mock orders, refunds and notifications in MongoDB; one open ticket per customer across channels. Tiers: a refund up to Rs 2,000 needs the customer's yes, above that and every VIP refund needs a human; deny refund above the order total, a second refund, and anything before identity is verified.
- **Channels:** web chat, voice page, email (AgentMail), WhatsApp (Twilio sandbox), the dashboard, and the simulated Paytm chat page. **Languages:** 11 via Sarvam, English inside. **Sarvam has no credits (402)**, so Hindi and voice fall back to English flagged `translation_failed`. Do not test or spend Sarvam.
- **Engine + packs** (`app/packs/`, `packs/<name>/`): a pack is `pack.yaml` + `policy.md` + `seed.json`; a deterministic condition grammar (no eval), generated validation models, engine-injected owner scoping, `deny_if` / `tiers` rules. **Librarian = zero Python files** (renew, fine waiver, catalogue search, plus the developer plugin `book_slot` that an admin grants). **Kirana = config + two plugin tools** (`plugins/kirana.py`: `get_resolution_options`, `issue_coupon`, and the `paytm_insights` feed).
- **Tool catalog + grants** (`app/grants.py`, dashboard "Tools" tab, admin only): per-pack grant and revoke, per-day cap, tighten-never-loosen tier, audited (`grant_audit`), a revoke blocks pending actions, the model only sees granted tools. **Roles** (`REVIEWERS`, `ADMINS` in `.env`, plus pack reviewers such as "Sharma" (merchant)) are **labels checked by name, not authentication**.
- **Kirana retention loop (all SIMULATED, fake data, not affiliated with Paytm):** a complaint about the Rs 240 purchase shows refund Rs 240 vs coupon Rs 288 (min(300, floor(240 x 1.2))) side by side; the customer picks; `issue_coupon` is **not granted at start** (an admin grants it in the Tools tab with a cap); "Run Paytm insights" proposes Rs 30 win-back coupons to lapsed opted-in regulars, the merchant approves, the offer lands in the customer's Paytm chat; the customer can redeem it ("Pay at Sharma Kirana (simulated)"); a dashboard strip shows issued, redeemed, budget and revenue brought back (simulated).

## 3. Measured results (final code)

- Customer service, 30 scripted English cases (`docs/results.md`): **30/30, 0 policy violations**, 7/7 in-policy resolved without a human, 7/7 expected escalations, median 4.99 s (p90 7.74 s). Scenario customers 8/8. **Final merge gate: 16/16 core cases, 0 violations, median 7.2 s.**
- Librarian **14/14**, 0 violations (`docs/results_librarian.md`). Kirana **28/28**, 0 violations: 22 rule cases with no model call and 6 real conversations, median 7.5 s (`docs/results_kirana.md`). Unit tests: 96 pass.
- Smoke finding: the translation model dropped a digit from an order number, so the system masks order numbers, amounts and dates before translating and falls back to English if a fact goes missing.

## 4. Commands

```
python -m pytest tests/ -q                                   # 96 unit tests, no model
python scripts/run_suite.py --core --base http://localhost:8000      # CS core, 16 cases (about 70 model calls)
PACK=kirana SUITE_PACE_S=1 python scripts/run_suite.py --base http://localhost:8001 --area kirana        # 22 rule cases, NO model calls
PACK=kirana SUITE_PACE_S=30 python scripts/run_suite.py --base http://localhost:8001 --area kirana-llm   # 6 conversations
PACK=librarian python scripts/run_suite.py --base http://localhost:8005                                  # 14 cases
python scripts/rehearse.py [kirana|librarian|both]           # walks the two demo beats through the APIs and checks each outcome (about 10 turns)
```
- The suite resets the instance's database before each case (`/debug/reset`, DEBUG=1) and writes `docs/results*.md`; **do not run two suites against the same results file at once** (lost update). After running the CS suite against 8000, `git checkout docs/results.md` to keep the committed full-run report. **Running a suite or `rehearse.py` against 8001/8005 resets that demo instance's data**: run `reset_demo.py --pack ...` afterwards.
- **For any future change to customer-service code,** run the core subset against a throwaway instance (`MONGO_DB=resolveai_gate PORT=8002 scripts/server.sh start`) and require 0 policy violations.

## 5. Model lanes and quota (`.env` `LLM_LANE_1..6`)

Order: 3.5-flash-lite, **Gemma 4 26B**, 3.1-flash-lite, 3.6-flash, Groq gpt-oss-120b, OpenRouter nemotron `:free`. **Gemma is the working lane**: it allows about 16K input tokens a minute shared by every server, so about 1.5 turns a minute. **Leave about 30 seconds between customer messages in rehearsals and the demo.** Groq's daily tokens and OpenRouter's 50 free requests were used up today; when every lane is cooling the router still tries them. Per-lane counters persist in `data/lane_usage.json` and show in the dashboard footer.

**Open decisions for the user:** (a) `.env` changed at 16:59 and the Gemini key in it behaves like a fresh quota (3.5-flash-lite and 3.6-flash answer although the local meter shows them used up). The assistant left the counters alone, so the router still skips those two lanes; resetting their counters in `data/lane_usage.json` is the user's call. The original plan said one key per provider. (b) Sarvam credits: the user is waiting for them.

## 6. What is left (needs the user)

1. **Rehearse** `docs/DEMO.md` in the browser: customer service beats 1-8 on 8000, librarian beat 7a at `http://localhost:8005`, kirana beat 7b at `http://localhost:8001` and `http://localhost:8001/paytm` (about 10 minutes in total; trim the opening and cross-channel beats if the slot is shorter). Two full rehearsals and the backup video.
2. **Slide deck:** `docs/PPT_PROMPT.md` is a self-contained prompt for another Claude (facts bank with IDs, slide-by-slide spec, honesty rules). Capture 4 screenshots.
3. Optional: a second insight segment ("make good" after a resolved complaint) was not built.

## 7. Honesty rules (slides, README, talk)

The Paytm channel, transaction data, insights and redemption are **simulated**; no Paytm APIs, data or partnership are used or implied; **text tag "via Paytm" only, no logo or lookalike UI**. Do not claim packs are built "in minutes", that http tools or MCP import work, or that roles are real authentication (all roadmap). The kirana pack is "config for everything except the custom coupon logic"; the librarian folder has zero Python files but opts into one developer plugin. A restated request ("please renew it") counts as consent, as in customer service (documented limit). Only measured numbers go on slides.

## 8. Gotchas

- `[hidden]{display:none!important}` is in the dashboard CSS; keep it.
- `.env` `DEMO_CUSTOMER_PHONE` contains a space, so sourcing `.env` in bash fails; read config through Python (`from app import config`).
- The Twilio sandbox URL kept saving as the bare domain; the server accepts signed Twilio POSTs at `/` as well as `/webhooks/twilio`.
- Seeded rows carry `seed: true` and the runner ignores them. CS `reset_demo` maps the user's real email and phone (from `.env`) onto the demo customer Priya; the suite uses `?fake=1`.
- Insights run on the button only; `issue_coupon` is ungranted at start by design. The Hindi-speaking kirana customer (C-102) gets English replies flagged `translation_failed` until Sarvam has credits; approve the English-speaking customer's insight (Imran) in the demo.
- Debug-only endpoints (`/debug/*`) need `DEBUG=1` in `.env`; remove it for a public deployment.
