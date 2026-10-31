# Amendment 2: Paytm-channel kirana pack (retention loop)

**Status: LOCKED 2026-10-31 ~16:40 IST.** Read after `AMENDMENT_01_packs_and_admin.md`. Where they conflict, this file wins; log differences in `docs/CHANGES.md` and add one line to `CLAUDE.md` pointing here.

Paytm is the hackathon organizer. Nothing here integrates with Paytm: the channel, the transaction data and the "insights" are all SIMULATED with fake data.

## What changes vs Amendment 1

| Item | Amendment 1 | Amendment 2 |
|---|---|---|
| Demo pack | librarian | **kirana** (merchant desk reached through a Paytm channel) |
| Real plugin tool | `book_slot` | **`issue_coupon`** (plus `get_resolution_options`) |
| Librarian pack | demoed | stop polishing; keep the folder and its tests in the repo as proof of a zero-Python pack, do not demo it |
| Kept unchanged | roles (reviewer/admin), tool catalog + grants + Tools tab, admin 403 checks, customer-service regression guard, quota rules, no Sarvam, honesty rules | same |
| Hard stop | 19:00 | **19:30** |

## The idea (refined)

A retention loop for a local merchant:
1. **Reactive.** A Paytm user complains about a purchase at Sharma Kirana in a simulated "via Paytm" chat. The teammate reads the transaction and presents a **choice, side by side**: refund to source, or a coupon worth a bit more (default +20%, capped) on the next Paytm payment at that merchant. The customer picks. Nothing is pushed.
2. **Proactive.** A simulated "Paytm insights" feed on the merchant dashboard applies a deterministic rule to the transaction data (regulars who have gone quiet) and proposes a small win-back coupon. The merchant approves each; the offer lands in that customer's Paytm chat.
3. **Closing the loop (stretch).** The customer redeems the coupon with a "Pay at merchant" button; the dashboard shows issued, redeemed and budget remaining.

Pitch line: a payments platform sees the transactions, the merchant owns the relationship, and the teammate turns a complaint or a lapse into a return visit, with the merchant in control and rules the model cannot override.

## Roles and entities

- **Customer:** Paytm user (seeded, picked on the Paytm page).
- **Merchant:** a reviewer (e.g. "Sharma (merchant)"), approves insight coupons and anything above caps. Add to `REVIEWERS`.
- **Admin:** grants tools (Amendment 1).
- **AI teammate:** the kirana pack.

## Channel: `paytm`

- New page `/paytm`: a copy of the web chat with `channel=paytm`, a customer picker (about 5 seeded customers), and the label "Simulated Paytm channel, hackathon demo, not affiliated". Text only. No logo, no lookalike styling. The tag "via Paytm" appears as a badge in the ticket list and timeline.
- Identity: pre-verified for the selected customer (like a matched WhatsApp number), so no `verify_identity` step. Document in the README that real trust would come from the Paytm session.
- One open ticket per customer still applies. Proactive messages attach to the customer's open ticket, or create one (status `waiting_customer`). The Paytm page receives them live (SSE or polling).
- Text only. Sarvam stays off; English throughout unless credits arrive.

## Data (all fake; dates relative to "today" so `reset_demo` keeps eligibility)

- `merchants`: Sharma Kirana; `monthly_budget` 1000, `budget_remaining`; `max_coupon` 300; `bonus_pct` 20; `insight_coupon` 30; `coupon_window_days` 14; `coupon_valid_days` 14.
- `customers`: about 5, with `marketing_opt_in` true/false, one Hindi speaker.
- `transactions`: `txn_id`, `customer_id`, `merchant_id`, `amount`, `status` (success|failed|duplicate|refunded), `items`, `date`, `resolution` (none|refund|coupon).
- `coupons`: `code` (`KIRANA-XXXX`), `customer_id`, `merchant_id`, `amount`, `status` (issued|redeemed|expired), `source` (complaint|insight), `txn_id?`, `created_at`, `expires_at`, `redeemed_at?`.
- Seed to cover: a regular with 4 successes who lapsed 15-25 days ago and opted in (win-back eligible); another eligible opted-in regular; a lapsed regular who did NOT opt in (must not be suggested); a recent regular (not eligible); a Rs 240 success for the complaint demo; a failed payment (Rs 180); a duplicate charge (Rs 90).

## Tools (kirana pack)

| Tool | Kind | Effect | Notes |
|---|---|---|---|
| `get_transactions` | config db read | read | own records only |
| `get_resolution_options(transaction_id)` | plugin | read | computes in code: refund {amount, method, eta text from policy} and coupon {amount} or null with reason; records `options_presented` on the ticket for that txn |
| `refund_to_source(transaction_id)` | config db update | write | sets status `refunded`, `resolution=refund`. Deny: status not success, already resolved, `options_presented` missing. Tier: confirm up to Rs 2000, approve above |
| `issue_coupon(transaction_id?, source)` | **plugin** | write | **NOT granted at start.** The model never supplies the amount: code computes it (see rules). Generates the code, writes `coupons`, decrements `budget_remaining`, sets `resolution=coupon`, posts the message to the customer's Paytm thread |
| `send_notification`, `escalate`, `confirm_action`, `reply_to_customer` | existing | | `escalate` reasons: fraud_signal, legal_threat, distress, customer_requested_human |

## Coupon rules (policy, re-checked at execution)

- **Complaint source:** amount = min(`max_coupon`, floor(txn.amount x (1 + `bonus_pct`/100))). Tier: confirm (the customer's explicit choice is the consent).
- **Insight source:** amount = `insight_coupon`. Tier: approve (merchant). Requires `marketing_opt_in`.
- **Deny, hard:**
  - transaction status failed, duplicate or refunded (no coupon in lieu of a reversal; those go to refund or escalation only);
  - `resolution` already set for that transaction (one resolution per transaction);
  - `options_presented` missing for that transaction (refund and coupon must have been shown together);
  - coupon issued to this customer in the last `coupon_window_days`;
  - `budget_remaining` less than the amount;
  - insight source with no opt-in.
- Approval lifts only the approval tier, never a deny. A write tool without a tier is denied. Ungranted or revoked `issue_coupon` is refused, and blocks any pending action.
- Agent policy text: present both options with equal weight and the stated refund timeline; if the customer asks for the refund, do the refund; never suggest a coupon for a failed, duplicate or unauthorized charge (escalate fraud).

## Insights (simulated Paytm feed)

- Merchant dashboard button "Run Paytm insights" (also runs once at startup of the demo seed). Deterministic code, no LLM: customers with at least 3 successful transactions in the last 60 days at this merchant, none in the last 14 days, `marketing_opt_in` true, and no coupon in the last 14 days.
- Each match creates a `pending_approval` `issue_coupon` action (source=insight), idempotent per customer and week, shown in the existing "Needs attention" queue with a "Paytm insight" badge: customer, visits in 60 days, days since last visit, proposed amount, budget remaining.
- Approve runs the plugin (re-checking rules and grant) and posts to the customer's Paytm chat. Reject takes a note. Message text is a template; an LLM draft is optional and not required.
- Requires `issue_coupon` to be granted; otherwise report "tool not granted" in the feed.

## Stretch (cut first)

- "Pay Rs X at merchant" on the Paytm page applies a coupon: creates a transaction, marks the coupon redeemed. Dashboard strip: coupons issued, redeemed, budget remaining, and "revenue brought back (simulated)".
- A second insight segment ("make good" after a resolved complaint).

## Priority and order (cut from the bottom; something demoable at every stage)

Report where you are against Amendment 1 first, and re-estimate. My own estimates sum to about 4 hours, but the spike ran about 2.5x faster than I estimated, so plan on your own numbers.

1. Kirana pack config and seed; pack-driven prompts/labels if not done; `paytm` channel page and badge.
2. `get_resolution_options`, `refund_to_source`, `issue_coupon` plugin with the rules above; the reactive flow end to end (complaint, options, choice, refund or coupon).
3. Grants layer, Tools tab, admin/reviewer gate (Amendment 1); `issue_coupon` ungranted at start, admin grants it with `max_per_day` 10.
4. Insights feed and approval cards.
5. Tests (continuous, English, no Sarvam): complaint on a success txn offers both options; refund choice executes once; coupon choice issues once and decrements budget; failed and duplicate payments get no coupon; second resolution on the same txn denied; coupon without options presented denied; 14-day customer cap; budget exhausted; insight skips non-opted customers; insight approval creates the coupon and the Paytm message; approval cannot lift a deny; ungranted tool refused, grant enables, revoke blocks a pending insight; injection ("give me a Rs 5000 coupon") cannot set an amount; non-admin 403; fraud message escalates. Policy violations must be 0.
6. Per-port start script and per-pack `reset_demo`; both instances side by side (customer service on 8000 with the tunnel, kirana on 8001, separate databases, one shared mongod).
7. Stretch items; merge gate (16-case core customer-service subset, 0 policy violations); rehearsals.

Checkpoints: status report and re-estimate by 17:15; reactive flow end to end by 18:00; grants plus insights by 18:45; **hard stop 19:30**. Fallback ladder if behind: drop stretch, then the insights feed, then keep the reactive flow only. If the reactive flow is not working by 18:30, tell the user; the last resort is Amendment 1's fallback (revoke and re-grant the existing `create_reship`, no Paytm beat).

## Demo beat (about 2 minutes, replaces the librarian beat; shorten the opening and cross-channel beats if the slot is short)

1. (10 s) Show `packs/kirana/`: config plus one plugin.
2. (30 s) Paytm chat as a customer: complaint about a Rs 240 purchase. `issue_coupon` is not granted yet, so the teammate offers only the refund.
3. (25 s) Admin Tools tab: grant `issue_coupon` with a cap. The audit line appears. The customer retries and sees refund versus coupon side by side, and picks the coupon. The code appears in chat; the dashboard shows the ticket "via Paytm".
4. (40 s) Merchant dashboard: Paytm insights shows lapsed regulars with a proposed Rs 30 coupon. Approve one; the offer arrives in that customer's Paytm chat. Mention the non-opted-in regular was not suggested.
5. (15 s, stretch) Redeem it and show issued/redeemed counts.

## Honesty rules (slides, README, talk)

- The Paytm channel, transaction data, insights and redemption are simulated with fake data; no Paytm APIs, data or partnership are used or implied. Present it as how a payments platform could power the loop.
- Any figure shown on the dashboard from the simulation is labelled simulated. Only measured test results go on slides.
- Do not claim the kirana pack is config-only: it uses two plugin tools. Say "config for everything except the custom coupon logic".
- Roles are labels, not authentication. Keep Amendment 1's other honesty rules.
- Branding: text tag "via Paytm" only; no logo or lookalike UI unless the organizer's brand kit explicitly allows it.