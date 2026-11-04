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

### In progress
- Blocked on human decisions: the 3 locked tests below, and the coverage test addendum.

### Locked tests that look wrong (not edited; human decision needed)
1. `test_PO_08_daily_cap_routes_to_awaiting_seller`: its helper opens two earlier cases with the
   same default payer, so with this case the buyer reaches 3 claims → PO-04 repeat-claimant hold.
   PO-13 puts the hold before the cap, so the code returns HOLD_FOR_REVIEW. Fix: give the helper's
   cases distinct payer ids.
2. `test_PP_05_retry_reuses_same_request_id`: it registers /track with `expect_ordered_request`
   next to a permanent token handler. pytest-httpserver checks ordered handlers first, so the token
   POST hits the ordered slot and the job fails permanently. Fix: add the token to the ordered
   list, or use non-ordered handlers for /track.
3. `test_PP_07_partial_refund_sends_amount_full_refund_sends_none`: passes alone. It fails after
   `test_PP_07_refund_timeout`, whose 1 s slow handler finishes into the shared httpserver log
   (3 refund requests seen). Fix: clear the log or wait for the slow handler in the timeout test,
   or filter by request id.

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
