# Doorstep — architecture decision records

Short ADRs: context, decision, trade-off. Newest at the bottom.

## ADR-001 — HOLD_FOR_REVIEW is a case state; cases get their own state machine

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** Hard rule 7 and FE-08 route contradicting evidence to `HOLD_FOR_REVIEW`, but the
  order state machine in SPEC §8 has no such state, so SM-01 would reject it. A single order can
  also have more than one problem over its life, and money decisions belong to a case, not an
  order.
- **Decision:** Add a separate case state machine:
  `OPEN → GATHERING_EVIDENCE → DECIDED → AUTO_REFUND | AWAITING_SELLER | HOLD_FOR_REVIEW |
  DISPUTE_EVIDENCE → SETTLED or CLOSED`. Order states stay unchanged. New criteria: SM-05 (only
  listed case transitions allowed) and SM-06 (property test: no case is `SETTLED` without a
  recorded decision and its approval path).
- **Trade-off:** Two state machines must stay consistent (order `CASE_OPEN`/`RESOLVED` vs case
  states), which needs an explicit sync rule. In return, review, approval and dispute paths are
  modelled explicitly and are testable without overloading the order lifecycle.

## ADR-002 — Day-2 test gate covers AS, AT, BR, MC, RG, RK, SE, PR, SB, E2E

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** BUILD_RUNBOOK.md's day-2 gate listed `CR, BR, MC, RG, RK, SE, E2E`. `CR` does not
  exist in ACCEPTANCE.md (the Control Room is `AS`), and the agent (`AT`), proof layer (`PR`) and
  sandbox proof (`SB`) criteria had no gate, so `scripts/trace.py` would flag them late.
- **Decision:** The day-2 gate is `AS, AT, BR, MC, RG, RK, SE, PR, SB, E2E`.
- **Trade-off:** Day 2 gets heavier (AT and PR are money-adjacent). SB criteria are manual/live
  checks, so their tests will be live-marked or checklist-backed rather than offline unit tests.

## ADR-003 — Money is integer minor units + ISO currency; limits per seller per currency

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** The spec said "default £/$50", which is ambiguous across currencies, and did not
  say how amounts are represented. Floats in a refund path risk rounding errors, and comparing a
  GBP refund to a USD limit is a silent bug.
- **Decision:** All money is an integer amount in minor units plus an ISO 4217 currency code; no
  floats anywhere in the money path. Auto-approve limits and daily refund caps are per seller and
  per currency in config, with defaults GBP 50 / USD 50 auto-approve and GBP 500 / USD 500 daily
  cap. Amounts in different currencies are never compared or summed. New criterion: PO-10.
- **Trade-off:** Conversion at every boundary (PayPal sends decimal strings, e.g. `"60.00"`), so
  parsing must be exact (Decimal → minor units) and tested; currencies with non-2-decimal minor
  units are out of scope for GBP/USD but the type must not assume 2 silently.
