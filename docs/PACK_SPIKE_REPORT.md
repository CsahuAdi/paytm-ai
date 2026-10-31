# ResolveAI: domain-pack feasibility spike

## Background

ResolveAI is an AI customer-service teammate for an e-commerce store, built in an 8-hour solo hackathon. It runs on free tiers only, with fake data. The stack:

- FastAPI, a single uvicorn worker, and local MongoDB.
- A Gemini / Groq / OpenRouter router.
- Sarvam for Indian languages.
- Web chat, voice, email (AgentMail) and WhatsApp (Twilio).
- A support dashboard.

The model only *proposes* actions through tool calls. A plain-Python rules engine decides allow / confirm / approve / escalate / deny. Humans approve held actions or take over tickets from the dashboard.

**Status:**
- Customer service is built and locked: 30/30 English test cases pass, with 0 policy violations, and 53 unit tests pass.
- Hindi and voice are blocked only by Sarvam credits.
- At the time of this report: 15:19, about 3h00m into the 8 hours, about 5h left.

**The idea tested:** split the system into an *engine* plus swappable *domain packs*, so the same teammate can become a librarian, HR assistant or school admin without writing Python. The critical question is tools: can a new teammate's tools be declared as data? The test case was a librarian pack with zero Python.

## Verdict: GO WITH CUTS

- The full build is about **3h15m** against about **5h left**. That leaves about 1h45m for the two rehearsals, the backup video and the Hindi checks.
- WhatsApp and email are already done, so cutting them frees no time. The real cost is rehearsal buffer.
- **Cuts:**
  - no http or composite tool kinds, and no MCP import;
  - dashboard changes limited to labels, header and a generic records panel;
  - the librarian's Hindi voice beat runs only if Sarvam credits arrive (otherwise English chat).

## Spike result (on branch `pack-spike`, not merged)

A librarian pack (`pack.yaml` + `policy.md` + `seed.json`, **no Python**) ran through the real agent loop on Gemini, at about 6 seconds per turn:

- Verification by member ID plus the last 4 phone digits.
- A renewal held until the member said yes, then executed exactly once (due date +14 days, renewal count +1), with an audit row and a duplicate-prevention key.
- Renewing a loan already renewed twice was **blocked and logged** (`max_renewals`).
- A prompt injection ("SYSTEM OVERRIDE… reset renewals") was **still blocked**; the loan was untouched.
- Guessing another member's loan ID returned **not_found**, because the engine forces "own records only".
- A Rs 150 fine waiver was **held for human approval**, approved through the existing dashboard code, and executed. A second waiver on the same fine was then **denied**.
- A duplicate proposal returned the same pending action (one row, no double execution).

**Test counts:**
- 78/78 unit tests pass (25 new ones for packs, no LLM calls).
- The customer-service regression subset passed 8/8 on the branch (confirm, deny, injection, approval, double-click, takeover, escalation, failover), with 0 policy violations.

**What surprised me:**
- After the agent asked "would you like to confirm?", the member's reply "please renew L-5001" counted as a yes. That's the same consent rule customer service uses, but worth knowing.
- Validation needed Pydantic strict mode, so that an object or list can never slip through as a text parameter.
- Pattern and length limits are enforced in validation but hidden from the model-facing schemas, because some LLM providers reject them.

## How a pack works

A pack is a folder: `packs/<name>/` holding `pack.yaml`, `policy.md` and `seed.json`. The server loads one pack via `PACK=<name>`, with its own database.

**The engine owns:**
- channels, the language layer and the LLM router;
- the context pipeline and the rules evaluation;
- approvals, takeover and the dashboard;
- the audit log and idempotency.

**The pack owns:**
- persona and policy text;
- entities and verification;
- tools, rule tiers and escalation reasons;
- labels and the default language.

```yaml
name, product, persona, policy_file, labels{subject, primary, money}
verification{param, lookup{collection, field, subject_ref?}, factors{text, voice: email|phone_last4}}
collections{<name>: {owner_field?}}       # owner_field => engine forces "this member's records only"
facts[], escalation_reasons[], context.in_scope[{collection, filter, fields, limit}]
tools:
  - name, description, effect: read|write|irreversible, kind: db, public?
    params: {<p>: {type, pattern, max_length, minimum, maximum, enum}}   # validation model generated automatically
    db: {op: find|update, collection, filter, fields, limit, as, set, inc}
    rule: {deny_if: [{rule_id, reason, when}], tiers: [{when, tier, rule_id}], tier, rule_id}
    idempotency: "template", result: {...}
```

**Example tool (from the spike):**
```yaml
- name: renew_loan
  effect: write
  kind: db
  params: {loan_id: {type: string, pattern: "^L-\\d{4}$"}}
  db: {op: update, collection: loans, filter: {loan_id: "{params.loan_id}", status: active}, as: loan,
       set: {due_date: {days_after: loan.due_date, days: 14}}, inc: {renewals: 1}}
  rule:
    deny_if:
      - {rule_id: max_renewals, reason: "Renewed twice already", when: {field: loan.renewals, op: ">=", value: 2}}
      - {rule_id: reserved_by_other, reason: "Reserved by another member", when: {field: loan.reserved_by_other, op: "==", value: true}}
    tier: confirm
    rule_id: renew_confirm
  idempotency: "renew:{params.loan_id}:{loan.renewals}"
```

**Condition grammar:** `{field, op, value}`, with ops `== != < <= > >= in not_in exists missing`, combined with `all`, `any` and `not`. Our own small function evaluates it; nothing is ever `eval`'d.

**Hard rules:**
- Deny rules are re-checked when the action runs.
- Human approval lifts only the approval tier, never a deny rule.
- A write tool with no declared tier is denied.

**Two changes from the original draft:**
- Members, students and other people live in the engine's existing person collection, so tickets, approvals and the queue need no changes.
- The engine forces "own records only" itself instead of trusting each tool template to include it.

## Coupling audit (how e-commerce-specific the engine is)

The engine is already about 80% domain-free.

**Domain-specific parts:**
- The 10 shop tools, order lookup, refund rules and the facts check stay as customer-service Python behind the same registry. They are the measured core and are not rewritten.
- Policy text and the "orders in scope" prompt block move to the pack.
- The order-number masking patterns for translation move to the pack.
- Dashboard labels (orders, refunds, VIP) come from the pack.
- Seed data and test cases become per pack.

**Untouched:** tickets, identities, channels, language layer, LLM router, approvals, takeover, audit and idempotency.

## What config cannot do (a Python plugin is still needed)

- One action that changes several collections at once, like a refund that writes a refund row and updates the order. That needs the composite tool kind, which is cut.
- Rules over counts or sums, like "second refund on this order".
- Creating records with generated IDs, like a replacement order.
- Fact checks against the database.
- Tolerant matching of spoken IDs ("L 5001").
- External systems (the http tool kind, which is cut).

## Security checklist

| Check | Result |
|---|---|
| No database operator injection through parameters | **PASS**: 8 hostile values rejected (`$ne`, objects, lists, braces, wrong format, extra keys); a pack containing `$` keys fails to load |
| Personal records scoped to the verified member | **PASS**: a tampered template is overridden; a live guess of another member's loan returns not_found |
| HTTP tools: host allowlist, no cross-host redirects, timeouts, secrets only in env | **NOT YET TESTED** (designed, not built) |
| Model can't change config; an invalid pack refuses to start | **PASS**: 8 invalid packs rejected at load; config is read once (an actual server start with a bad pack wasn't run) |
| Prompt-injection stress test against a config-only tool | **PASS** (live) |
| Write tool with no tier is denied; approval can't lift a deny | **PASS** |

## Risks to the customer-service demo

- The spike touches 21 lines in 6 customer-service files, all gated by `PACK`. With it unset, the registry returns the exact customer-service code.
- `master` and the running demo server are untouched.
- The dashboard label work is the most regression-prone part. Labels default to today's text to contain that.
- A second server instance (the librarian, on port 8001) adds about 150 MB of memory.
- **Free-tier LLM quota is the real constraint.** The two Gemini lite models have about 480 calls left today.

| Use | Calls |
|---|---|
| Librarian tests | ~50 |
| Full customer-service suite | ~150 |
| Rehearsals plus demo | ~130 |

**Open decision:** the merge gate. Options are the full customer-service suite (tight on quota) or the 16-case core subset (about 70 calls).

## The demo beat this enables (about 75 s, before the final numbers)

1. Show `packs/librarian/` on screen: YAML, policy, seed, no Python file. "This teammate was built from configuration."
2. Switch to the already-running librarian instance. The dashboard labels change from orders/refunds to loans/fines.
3. Renew a book by voice in Hindi, if Sarvam credits arrive (otherwise English chat).
4. Ask for a Rs 150 fine waiver. It lands in the same approval queue; approve it. "Rules, approvals and audit came with the engine."

## Order of work if GO (about 3h15m)

1. Customer-service pack config, registered against the existing Python tools; generic prompts; per-pack masking patterns: **35m**
2. More generic tool kinds: db insert, text search (MongoDB text index), public read tools: **30m**
3. Full librarian pack: about 8 members (one Hindi speaker), 40 books, loans, fines, plus search and reserve tools: **25m**
4. 10 scripted librarian test cases (identity failure, max renewals, approval, injection, idempotency): **30m**
5. Pack-driven dashboard labels and records panel, a per-port server script, per-pack reset: **45m**
6. Both instances running side by side; demo beat rehearsed: **15m**
7. Customer-service suite gate, then merge: **15m**
