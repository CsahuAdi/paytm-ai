# ResolveAI

An AI customer-service teammate that resolves tickets end to end under rules the model cannot override, and hands off
to humans with context. Built in an 8-hour hackathon on free tiers with invented data.

- **Customer service** (the measured product): web chat, voice, email and WhatsApp; refunds, replacements and
  cancellations decided by a plain-Python rules engine; reviewer approvals and takeover. See `docs/results.md`.
- **Domain packs**: the same engine runs other teammates. `packs/librarian/` is configuration only (pack.yaml,
  policy.md, seed.json). Logic that configuration cannot express (several collections changing at once, rules over
  counts, generated IDs, external systems) is written by developers as plugin tools in `plugins/`, as customer service
  and `book_slot` do.
- **Kirana pack and the simulated Paytm channel** (`packs/kirana/`): a neighbourhood store's desk reached through a
  text-only "via Paytm" chat page. A complaint gets a refund-or-coupon choice side by side (coupon amount set by store
  policy, never by the model); a deterministic "Paytm insights" rule proposes win-back coupons that the merchant approves.
  **The Paytm channel, transaction data, insights and (stretch) redemption are all simulated with fake data: no Paytm APIs,
  data or partnership are used or implied, and every figure from the simulation is labelled simulated.** The kirana pack is
  config for everything except the custom coupon logic, which is two developer plugins (`plugins/kirana.py`). The selected
  customer is pre-verified like a matched WhatsApp number; real trust would come from the Paytm session.
- **Roles**: AI teammates act within rules and granted tools; reviewers approve held actions and take over tickets;
  admins grant, cap and tighten tools per teammate (audited). **These are demo-level roles chosen from a dropdown,
  not authentication; real login is roadmap.**

## Run

```
scripts/server.sh start                                   # customer service on :8000
PACK=kirana PORT=8001 scripts/server.sh start             # kirana desk on :8001 (its own database); simulated Paytm chat at /paytm
PACK=librarian PORT=8005 scripts/server.sh start          # librarian on :8005 (its own database)
python scripts/reset_demo.py [--pack kirana|librarian]    # restore seed data
python -m pytest tests/                                   # unit tests (no LLM)
python scripts/run_suite.py --core                        # scripted cases against :8000
```

Demo runbook: `docs/DEMO.md`. Scenarios: `docs/SCENARIOS.md`. Deviations and findings: `docs/CHANGES.md`.

## Roadmap (not built)

Real login and permissions, http and composite tool kinds, importing tools from MCP servers, a request-a-tool flow,
SLA timers, real phone calls (Exotel). Packs are not "built in minutes": a pack still needs its data, policy, rules
and tests.
