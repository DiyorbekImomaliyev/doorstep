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

- Phase C (implement until green) for RL, SM, PO, FE, PP, WH, in that order, one commit each:
  c906032 RL · 775f7a3 SM · 7719808 PO · 0a12d57 FE · 1bdb6a2 PP · cb61a73 WH · 4cda458 SM fix +
  PP-03 recording · 8f6bcce PP debug_id. Pinned SDK added (ADR-014); `paypal-plan.md` written.
  ADR-015 to ADR-019.
- Checks:
  - `make lint` clean (ruff, pyright, 3 import contracts kept);
  - `make locked` OK;
  - `trace.py --prefixes PP,WH,SM,RL,FE,PO` 62/62 (full trace fails only on later-phase PR/E2E IDs);
  - `make test-integration` 4 passed;
  - `make test`: 436 passed, 1 xfailed, **3 failed** (locked tests that look wrong; see below).
- Coverage (branch): `app/orders/state.py` 100%; `app/policy` 89% (rules 92%, service 88%,
  gateway 93%). Not at 100%: the missing branches are error and defensive paths no locked test
  reaches. Needs a human-approved test addendum.
- Live sandbox (direct REST): OAuth OK; order create OK; 404 mapped with `debug_id`; real 422
  INSTRUMENT_DECLINED recorded at `recordings/paypal/capture_declined.json`.
- `/paypal:doctor`: no critical findings. Fixed one warning (`debug_id` was not logged).
  Open warning: `PAYPAL_WEBHOOK_ID` unset.

- Locked-test fixes (human-approved, 7271c42). The coverage addendum
  `tests/unit/test_po_policy_errors.py` (5e218a6) found 3 money-safety bugs, now fixed (ADR-021):
  - a decline while a refund is queued is refused;
  - a CLOSED or SETTLED case never sends money (pre-refund re-check);
  - a refund job with a missing row fails permanently (audit row, ERROR log, case held when it
    still exists); orphans are listed by `app.worker.orphans.orphaned_refund_jobs`;
  - the worker survives any single bad job.
- Deterministic gate (ADR-020): `make test` uses a fixed Hypothesis seed; `make test-explore`
  runs random seeds. Approve/decline wrong-state errors are `IllegalTransition`s, which fixes
  the flaky `test_SM_04_no_refund_without_paid_property`.
- Checks after the fixes:
  - `make lint` clean;
  - `make test` 476 passed, 1 xfailed, 3 runs in a row;
  - `make test-explore` 3 random-seed runs green.
- Branch coverage: `state.py` 100%; `app/policy` 99%. The one partial branch is
  `service.py` `_fail_dangling`, the "case row gone" path: reachable by fault injection, but no
  test yet.
- mutmut on app/policy (`make mutmut`; macOS needs forkserver + `OBJC_DISABLE_INITIALIZE_FORK_SAFETY`):
  - 1441 mutants: 1085 killed, 356 survived (75%);
  - 254 survivors change only strings, logs, audit text or args to None;
  - about 102 change logic, mostly assertion gaps (daily-cap boundary and filters, dispute
    scoped to the order, the bad-address override, refund kind on external refunds);
  - a test addendum is needed (human approval).

### In progress
- Waiting on the human: tag move for the xfail-mark removal; approval of the
  mutation-killing and `case_id` fault-injection tests.

### Not started (day-1 definition of done)
- [ ] Sandbox order with shipping options → capture → tracking → partial refund → sandbox dispute settled
- [ ] Shippo test webhook received
- [ ] Repo, Render blueprint and CI skeleton live

### Notes
- Not built yet (later phases): FE-09 dataset (`evals/datasets/failure_cases.jsonl`,
  thresholds.yaml); worker handlers for PayPal capture/refund/dispute webhook events (events are
  stored in the inbox, then ignored).
- PayPal sandbox MCP fails on every tool with `PAYPAL_API_SETUP_ERROR: Unsupported cache mode:
  default`. This is a server-side bug at PayPal; the token is valid via REST. Report it upstream.
- `PAYPAL_WEBHOOK_ID` is not set yet; webhook verification can't be tested live until it is.
- Phase D must add a contract test: fake PayPal responses match recorded sandbox shapes.
