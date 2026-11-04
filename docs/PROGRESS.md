# Doorstep — progress log

Tracked against the day's definition of done (SPEC section 16).

## Nov 4, 2026 — Core loop (build day 1)

### Done
- Phase A (spec review) for PP, WH, SM, RL, FE, PO: gaps listed, decisions approved, ACCEPTANCE.md
  and SPEC.md updated; ADR-001 to ADR-011 in docs/DECISIONS.md.
- Case state machine, explicit order/case transition tables, money as minor units (GBP/USD only),
  address validation after approval with PATCH before capture.

### In progress
- Phase B (tests first) for PP, WH, SM, RL, FE, PO.

### Not started (day-1 definition of done)
- [ ] Sandbox order with shipping options → capture → tracking → partial refund → sandbox dispute settled
- [ ] Shippo test webhook received
- [ ] Repo, Render blueprint and CI skeleton live

### Notes
- Tag `tests-locked-core` was created before any tests existed; it must be moved to the commit
  with the approved tests.
- No local Postgres yet (`brew install postgresql@16`); Postgres-only tests (SM-08, WH-03 race,
  PO-07 concurrent) need it.
