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

## ADR-004 — Auto-approve limit is inclusive ("at or under")

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** The spec said "under the limit" and "over the limit", leaving an amount exactly
  at the limit undefined.
- **Decision:** An amount at or under the seller's auto-approve limit (same currency) auto-refunds.
- **Trade-off:** A £50.00 refund is automatic under the default; sellers who want a strict cap
  set their limit one minor unit lower.

## ADR-005 — Extra case transitions and an explicit order transition table

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** ADR-001's case flow had no path for "reviewer needs more evidence", a blocked or
  failed refund, a dispute opening mid-flow, or a seller declining. SM-01 also pointed at a single
  arrow chain rather than a real transition list.
- **Decision:** Add `HOLD_FOR_REVIEW → GATHERING_EVIDENCE`; `AUTO_REFUND | AWAITING_SELLER →
  HOLD_FOR_REVIEW`; `AUTO_REFUND | AWAITING_SELLER → DISPUTE_EVIDENCE`; `AWAITING_SELLER →
  CLOSED`. SPEC §8 now has explicit order and case transition tables, a sync rule (case opened →
  order CASE_OPEN; case SETTLED/CLOSED → order RESOLVED; one open case per order), and handling
  for repeat (no-op) and backward (recorded, no transition) carrier events. `PACKED` is optional
  (`PAID → SHIPPED` allowed) because the packing check is on the cut list. `EXCEPTION` may go to
  `DELIVERED`/`PICKED_UP` because a rebooked delivery can arrive with no transit scan.
- **Trade-off:** More transitions to test; `RESOLVED` is terminal, so a second problem after
  resolution is out of scope for the hackathon.

## ADR-006 — Money limits live in `rules/money_limits_v1.yaml`

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** Hard rule 2 puts business rules in `rules/*.yaml`; ADR-003 said "config".
- **Decision:** `rules/money_limits_v1.yaml` (with `id`, `version`, `source`) holds default
  auto-approve limits and daily caps per currency and the repeat-claim threshold (3 in 90 days).
  Per-seller overrides live in the `sellers` table. These values never appear in Python or in
  `tuning/engine_config.py`, so the autoresearch loop cannot change them.
- **Trade-off:** One more rule file to validate at startup; changing a default needs a PR.

## ADR-007 — Validate the address after PayPal approval, PATCH before capture

- **Date:** 2026-11-04 · **Status:** accepted (human-approved); PATCH to be verified live
- **Context:** The shipping address usually comes from the buyer's PayPal approval, so it cannot
  be checked "when checkout opens". Shipping to an address that differs from the PayPal order
  voids Seller Protection (RL-07).
- **Decision:** After approval and before capture, validate the address PayPal returned. If a fix
  is needed, the buyer confirms it, Doorstep PATCHes the order's shipping address
  (`PATCH /v2/checkout/orders/{id}`), then captures. Never capture an unvalidated address unless
  the buyer explicitly overrides (stored as a signal). New criteria: CK-06, PP-10.
- **Trade-off:** An extra buyer step between approval and capture. If the sandbox rejects the
  PATCH, stop and record it here, then use the SPEC §17 fallback.

## ADR-008 — GBP and USD only

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** Launch markets are the UK and US; limits and caps are per currency.
- **Decision:** Only GBP and USD are supported; any other currency is rejected at checkout
  (PO-10).
- **Trade-off:** EU (EUR) needs a config change and new limit defaults later; the money type
  still carries a currency code so adding one is not a refactor.

## ADR-009 — Disputes: provide-evidence only

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** The spec listed provide-evidence, make-offer and accept-claim; time is short.
- **Decision:** Build `provide-evidence` (PP-09). `make-offer` stays an `xfail` limitation;
  `accept-claim` is not built.
- **Trade-off:** Sellers settle open disputes in PayPal directly for offers.

## ADR-010 — Failed refunds: retry with the same key, then hold for review

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** A refund can fail transiently (5xx, timeout) or permanently (4xx, e.g. balance).
- **Decision:** 5xx and timeouts are retried with the same `PayPal-Request-Id` up to
  `max_attempts` (PP-07). Only then is the `money_action` marked `failed` and the case moved to
  `HOLD_FOR_REVIEW`. A 4xx fails at once and goes to review. Never retry with a new key (PO-12).
- **Trade-off:** A transient outage delays the refund by the backoff window; no risk of a double
  refund.

## ADR-011 — Phase A gap fixes for PP, WH, SM, RL, FE, PO

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** Phase A review found untestable or missing criteria in the core modules.
- **Decision (summary; full text in specs/ACCEPTANCE.md):**
  - Idempotency key = `{entity}:{id}:{action}[:{money_action_id}]` (PP-05).
  - `paypal_client` split into `reads`/`writes`; only `app/policy` and `app/worker` import
    `writes` (PO-01).
  - PayPal verify endpoint down → 503, nothing stored (WH-06); Shippo wrong token → 404 (WH-04);
    Shippo event id derived as `sha256(tracking_number|status|status_date)` if the payload has
    none, to be verified live (WH-08).
  - Rule version is a quoted string; no matching rule set → `HOLD_FOR_REVIEW` (RL-02, RL-03);
    US decisions use structured policy fields, never an LLM (RL-06).
  - Engine is deterministic code; buyer-blaming outcomes go to the seller, never auto-deny
    (FE-10, FE-11).
  - Fixed check order in the policy service (PO-13); daily cap uses UTC days (PO-08); repeat
    claims counted per PayPal payer id per seller (PO-04).
- **Trade-off:** About 20 more criteria to test on day 1, in exchange for fewer surprises in the
  sandbox and no ambiguous money behaviour.

## ADR-012 — `approvals` and `disputes` tables

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** SM-06 requires an approval record before a case can settle; PO-05 needs to know
  whether a PayPal dispute is open. Neither fit an existing SPEC §8 table.
- **Decision:** Add `approvals` (case, decision, approver kind/id, rule id) and `disputes`
  (order, PayPal dispute id, status) to the data model. `money_actions` gains `order_id`,
  `decision_id`, `status` and `external` (refunds made outside Doorstep), with a unique
  constraint on (decision_id, kind): one refund per decision, so concurrent approvals cannot
  double-refund (PO-07) while re-review can still issue a second partial refund.
- **Trade-off:** Two more tables to migrate and keep in the audit trail.

## ADR-013 — Refund execution, test doubles and the test database

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Decision:**
  - Refund and dispute writes live in `app.paypal_client.writes.money`, importable only by
    `app/policy`. The worker runs refund jobs by calling app/policy (`RefundJobHandler`),
    never the money-writes module (PO-01 contracts).
  - PayPal is faked with a local HTTP server (pytest-httpserver), so tests are independent of
    whether the Orders/Payments calls use the official Server SDK or REST. In phase D the real
    sandbox responses are recorded under `recordings/` and a contract test checks the fake's
    responses have the same shape.
  - Postgres-only tests run against `<DATABASE_URL db>_test` (or `TEST_DATABASE_URL`) and refuse
    any database whose name does not end in `_test`, because they drop all tables.
- **Trade-off:** Fakes can drift from PayPal until the phase-D contract test exists.
