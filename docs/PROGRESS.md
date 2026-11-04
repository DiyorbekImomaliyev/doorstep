# Doorstep — progress log

Tracked against the day's definition of done (SPEC section 16).

## Nov 4, 2026 — Core loop (build day 1)

### Done
- Phase A (spec review) for PP, WH, SM, RL, FE, PO: gaps listed, decisions approved, ACCEPTANCE.md
  and SPEC.md updated; ADR-001 to ADR-011 in docs/DECISIONS.md.
- Case state machine, explicit order/case transition tables, money as minor units (GBP/USD only),
  address validation after approval with PATCH before capture.

- Phase B (tests first) for PP, WH, SM, RL, FE, PO: 166 test functions (444 with parameters),
  all 62 core acceptance IDs traced; test names approved. Tests fail for the right reason
  (NotImplementedError stubs, missing rule files, phase-D recording); 15 guard tests pass.
- Local Postgres 16 (Homebrew): `doorstep` (dev, matches .env) and `doorstep_test` (tests).
  The 3 Postgres concurrency tests run against `doorstep_test`.
- ADR-012 (approvals, disputes), ADR-013 (refund execution, fakes, test DB).

- Tag `tests-locked-core` moved to 55d625a and pushed (human).
- PayPal sandbox token generated (REST OAuth works). ADR-014: PayPal SDK = APIMatic git SDK pinned
  to 143e57f; SDK code follows paypal-plan.md + the APIMatic workflow.
- `.claude/settings.json` committed (plugin enablement only, no secrets); `settings.local.json`
  ignored.

### In progress
- Next: phase C (implement until green). Not started.

### Not started (day-1 definition of done)
- [ ] Sandbox order with shipping options → capture → tracking → partial refund → sandbox dispute settled
- [ ] Shippo test webhook received
- [ ] Repo, Render blueprint and CI skeleton live

### Notes
- PayPal sandbox MCP fails on every tool with `PAYPAL_API_SETUP_ERROR: Unsupported cache mode:
  default`. This is a server-side bug at PayPal; the token is valid via REST. Report it upstream.
- `PAYPAL_WEBHOOK_ID` is not set yet; webhook verification can't be tested live until it is.
- Phase D must add a contract test: fake PayPal responses match recorded sandbox shapes.
