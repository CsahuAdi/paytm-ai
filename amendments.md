# Amendment 1: domain packs, tool grants, admin role

**Status: LOCKED 2026-10-31 15:59 IST.** Supersedes my two earlier chat messages about the pack-spike plan. Where this file conflicts with `PLAN.md` or `CLAUDE.md`, this file wins; log the difference in `docs/CHANGES.md`. Add one line to `CLAUDE.md` pointing here.

Customer service is built, tested and locked. It must behave exactly as before when `PACK` is unset. Work on branch `pack-spike`; merge only through the gate below.

## Context

Spike verdict was GO WITH CUTS. A config-only librarian pack (no Python) ran through the real loop with confirm, deny, injection, approval and idempotency all behaving. This amendment adds a tool catalog with per-pack grants and an admin role, so the demo can show: developers write plugin tools once, admins (no code) decide which teammate may use them.

## Roles (demo-level auth, no passwords)

| Role | Who | Can do |
|---|---|---|
| AI teammate | a pack (customer service, librarian) | talk to the public, act within rules and granted tools |
| Reviewer | named person | approve/reject one held action, take over/hand back a ticket (existing behaviour) |
| Admin | named person | grant/revoke tools per pack, set caps, tighten tiers |

- Names come from env: `REVIEWERS=...`, `ADMINS=...` (comma lists). The dashboard dropdown shows each name with its role. The Tools tab is visible to admins only.
- **Server side check too:** grant/revoke endpoints reject (403) an acting name not in `ADMINS`; approve/reject/takeover reject names not in `REVIEWERS` or `ADMINS`. This is a label, not authentication; say so in the UI footer and README ("demo-level roles; real login is roadmap").
- Case approval stays single-level. The admin is a configuration role, not an extra approval level.
- Neither role can override a deny rule. An admin can only tighten a tool, never loosen it past its manifest.

## Tool catalog and grants

- Every tool has a manifest: name, description, params, effect (read|write|irreversible), loosest allowed tier, required env secrets. Plugins register with a decorator in `plugins/`; config tools register from `pack.yaml`.
- Collection `tool_grants`: `{pack, tool, enabled, tier, max_per_day, granted_by, granted_at}`. Tier may only be equal to or tighter than the manifest's loosest tier (order, loosest to tightest: allow, confirm, approve).
- Each turn the model's tool list = catalog AND enabled grants. Read grants from Mongo each turn with a small cache invalidated by the in-process pub/sub.
- The executor re-checks the grant and the daily cap at run time, like deny rules. A revoked tool blocks even an already pending action (log the reason).
- Every grant/revoke/cap/tier change writes an audit row (who, when, before, after), shown in the Tools tab.
- **Customer service regression guard:** with `PACK` unset, all customer-service tools are auto-granted at startup with their current tiers. Nothing about its behaviour changes. For the librarian pack, all config tools are granted by the seed; `book_slot` ships loaded but NOT granted.
- Admins never upload code. Plugins are written and deployed by developers. Do not hot-load Python files.

## New plugin tool: `book_slot`

Params: `resource_id`, `start` (ISO datetime), `duration_min` (enum 30|60|90|120). Logic config cannot express: conflict check over existing bookings, generated booking ID `BK-####`, write to `bookings`, confirmation email through the existing AgentMail path. Manifest loosest tier: confirm. Seed a `study_rooms` collection. If AgentMail is unavailable, store the email in an outbox record and show it in the Tools tab so the demo never depends on an inbox.

## Scope and order (estimates are mine; confirm yours before starting)

1. Pack-driven prompts, ID/number masking patterns and dashboard labels; customer-service defaults byte-for-byte unchanged. (15 min)
2. Finish the librarian pack, zero Python: about 8 members (one Hindi speaker), a small catalog, loans (one overdue, one renewed twice, one reserved by another member), fines of Rs 60 and Rs 150, `study_rooms`. Tools: get_member_loans, renew_loan, waive_fine (confirm up to Rs 100, approve above, deny if no fine), send_notification, escalate. Escalation reasons: lost-book dispute, harassment, distress. (20 min)
3. Catalog, grants, Tools tab, `book_slot`. (70 min)
4. Admin and reviewer role gate (env lists, server checks, dropdown tags, Tools tab visible to admins). (10 min)
5. Tests (30 min), English only, no Sarvam:
   - librarian: identity failure, max renewals, reserved-by-other, approval, injection, idempotency;
   - grants: ungranted tool refused, grant enables it, revoke blocks a pending action, daily cap enforced, tier cannot be loosened, non-admin gets 403.
6. Dashboard: pack header and labels, raw record JSON in the action detail (no pack-specific records panel), per-port start script, per-pack `reset_demo`. (25 min)
7. Both instances side by side (customer service on 8000 with the tunnel, librarian on 8001, separate databases, one shared mongod, one worker each), rehearse the beat. (15 min)
8. Merge gate: the 16-case core customer-service subset (about 70 calls), 0 policy violations, then merge. Full suite only if quota is clearly left after the final rehearsal. (15 min)

**Cut:** search_catalog, a large book catalog, a pack-specific records panel, the request-a-tool flow, db insert, http and composite kinds, MCP import.

## Checkpoints and stops

- 17:15: librarian pack renews and waives through the real loop.
- 18:15: grants plus `book_slot` working end to end.
- **19:00: hard stop.** If the demo beat is not working end to end, stop, merge nothing, report. If grants are not working by 18:15, fall back to revoke/re-grant of the existing `create_reship` in customer service with no new plugin.
- 19:00-20:15: merge gate, rehearsals, backup video, and Hindi checks if Sarvam credits have arrived.
- Reserve at least 200 Gemini calls for rehearsals and the demo. Report remaining quota after each major step. Run librarian tests on the Groq lane if its tool calls are reliable. Do not spend or test Sarvam until told credits are available.

## Demo beat (about 90 s, before the final numbers beat)

1. (15 s) Show `packs/librarian/`: three config files, no Python.
2. (25 s) Librarian instance: renew a book by Hindi voice (English chat if Sarvam is still blocked). Dashboard labels read loans/fines.
3. (15 s) Ask: "Book study room B on Monday at 4." Refused: the tool is not granted; the agent offers to pass it to the desk.
4. (20 s) Tools tab as an admin: `book_slot` shows available but not granted. Grant with cap 5. The audit line appears.
5. (15 s) Retry: confirmation, booking ID, the confirmation email appears (inbox or outbox view).
6. (optional, 10 s) Revoke, ask again, refused.

Pitch line: AI teammates do the work, reviewers handle the exceptions, and admins decide what each teammate is allowed to do.

## Honesty rules (slides, README, demo talk)

- The librarian pack is config-only. Anything with logic config cannot express (several collections changing at once, rules over counts, generated IDs, external systems) uses a plugin, as customer service and `book_slot` do.
- Do not claim packs are built "in minutes", that http tools or MCP import work, or that roles are real authentication. These are roadmap.
- Known limit: a restated request ("please renew L-5001") counts as consent, as in customer service. Tighten later for sensitive packs such as HR.