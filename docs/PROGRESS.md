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

- Mutation addendum (9b22bbb, human-approved) made green:
  - reason codes: `app/policy/reasons.py` (`Reason` + `MESSAGES`), `decisions.reason_codes`,
    `paypal_response.blocked_code` (ADR-023);
  - refund identity: `custom_id = money_action.id` on every refund; crash recovery reads
    the capture's refunds and settles ours with no resend; unidentified refunds block
    (ADR-022);
  - a job payload without `money_action_id` fails permanently (orphan, ERROR log);
  - simplifications: unused `RefundJobHandler.rulebook` dropped, `_Verdict.target` only on
    blocks, `RefundRequest` built from its TypedDict (pyright-clean).
- tests/ diff vs 9b22bbb: only the 9 xfail marks (plus the blank line after the removed
  `pytestmark`).
- Checks:
  - `make lint` clean;
  - `make test` 563 passed, 1 xfailed, 3 runs in a row;
  - branch coverage: `app/policy` 100% (559 statements, 120 branches); `state.py` 100%.
- Mutation gate (ADR-023): `make mutmut-gate`, `scripts/mutmut_gate.py`,
  `.github/workflows/mutation.yml`; equivalents listed in `docs/mutation-report.md`.
  - Last run: 1417 mutants, 1272 killed, 145 survived: 31 text-only, 114 logic (91 audit-trail
    content, 23 logic gaps).
  - **The gate is red.** It needs a test addendum (human approval); see the report.

- Mutation-gap addendum (e8960d5, human-approved): 22 tests in
  `tests/unit/test_po_mutation_gaps.py`. They pin the refund amount (partial stays partial),
  custom_id on every refund, the crash-recovery conditions, refund kind, address comparison,
  and one audit golden per money or state action.
  - Simplifications: `explanation` left to its column default, no target on a finished-case
    block, `_load` checks each reference once.
  - Checks: `make lint` clean; `make test` 585 passed, 1 xfailed (3 runs); `app/policy`
    branch coverage 100%.
  - **`make mutmut-gate` green:** 1386 mutants, 1352 killed, 2 timeout, 32 text-only
    survivors, 0 logic survivors.

- Journey, frontend and agent work (this session), one commit each:
  4bbd106 CK/TR/BC/EX/PR · bdc9951 API, runtime, worker, model adapter, seed · e049081 PayPal
  return URL · 3ccf7af web (React + AG Grid) · deploy blueprint (ADR-028) · cfa533c PK packing
  check (ADR-029) · 9ba8665 MC MCP tool (ADR-030) · 3e0d875 AT case agent on the Agent Toolkit
  (ADR-031) · 436aeff Schemathesis fuzz, secrets checks, CI, full trace.
  - Fixes from review: development receipt key persisted beside the SQLite file (receipts kept
    verifying across restarts); seeded history dated in order; buyer progress never shows
    "Received" on a "Not here"; `/demo/seed?scratch=0` development-only; MCP URL without a
    redirect; a 500 on non-ASCII link tokens (found by Schemathesis).
  - Checks: `make lint` clean (ruff, pyright, 3 import contracts kept); `make test` 798 passed,
    24 strict xfail; `make fuzz` 26 operations clean; `make trace` 129/129; Playwright 4 passed;
    `app/policy` + `state.py` branch coverage 100%.

- Nov 4 (later): RK risk model (f8bb8c0, ADR-032), Control Room risk column (2983515), RG policy
  reader (e9090d3, ADR-033), mutation tests for the agent gate (accee13).
  - Risk model: XGBoost with monotone constraints, Platt-calibrated, exact TreeSHAP reasons;
    synthetic data; PR-AUC 0.155 (ceiling 0.156), ECE 0.005; `make risk-model` rebuilds it
    deterministically, and a test fails if the tuning changes without a retrain.
  - Policy reader: BM25 passages, cited structured answer, rule-file quote when nothing matches.
  - Checks: `make lint` clean; `make test` 834 passed, 19 strict xfail; `make fuzz` clean;
    trace 129/129; Playwright 5 passed; mutmut on `app/policy`: 0 logic survivors.

- Nov 5: live deploy at https://doorstep-ojmg.onrender.com (healthz ok, model configured).
  - `make smoke` passed against the real sandbox (US seller app; the first app's account country
    refused every payment with COMPLIANCE_VIOLATION): order 53D940861H351721X captured
    0F366549UK2172723 -> tracking -> "Not here" -> us_seller_policy_v1 AUTO_REFUND -> PayPal
    refund 7JK794112L462524K completed -> receipt chain ok.
  - PayPal sent PAYMENT.CAPTURE.COMPLETED to the live webhook; a forged event is rejected (400).
    A wrong Shippo token is rejected (404).

## Nov 5, 2026 — v2 liability router (SPEC section 7a)

### Done
- d685a53 spec v2 (ADR-035..039). f37e719 SL possession seal, RT rights card, SA-04.
  de37d81 SA delivered-scan audit. dff214b RC carrier recovery.
- DE dispute evidence (ADR-038): the seller's tap creates one `evidence` money action and an outbox
  job with a `PayPal-Request-Id`. The job calls provide-evidence with facts only (tracking, a
  counted seal scan, the audit verdict, the verify link). Retries reuse the id. Two receipts
  record the tap and the exact notes (SHA-256).
- CF counterfactual and prevention:
  - Each decision stores which proof from its rule file would have changed the liable party. A
    property test checks that adding a named proof really proves possession.
  - Prevention suggestions group the seller's own seller-liable cases by method and value band.
- Web:
  - seal page (`/s/:token`) and printable seal card;
  - rights card on the buyer page;
  - case drawer panels: scan audit, carrier recovery, send evidence, counterfactual;
  - Control Room: "Recovered from carriers" KPI and prevention tips.
- Demo seed, additive:
  - Sam's Royal Mail "Delivered" scan comes from E8 in the same minute as two of Maria's other
    parcels (simulated, labelled), so after "Not here" the audit is contradicted and a £60 claim
    is prepared;
  - a US order whose seal was scanned has an open simulated PayPal dispute.
- Found and fixed:
  - Mutation gate gaps: RC was committed before the gate ran on the new `carriers.py`. Its 5
    survivors and evidence.py's 75 are now pinned by tests (exact audit rows, request id and
    dedupe key, window per claim type) or marked as equivalent mutants with a reason.
  - Receipt chain race (ADR-040): parallel demo seeds on Postgres failed on the unique `seq`.
    Appends now wait on a transaction-scoped advisory lock.
  - UI review from screenshots:
    - When possession was proven, the case text said both "the buyer is responsible" and
      "recommended fix: refund $89.00". It now says the risk sits with the buyer, the rule sets
      no fix, and the seller may still refund;
    - the batch-scan finding reads as plain words ("no other parcel ... within 60 s");
    - the Status column no longer cuts off "Marked as delivered".
- Checks:
  - `make lint` clean (3 import contracts kept);
  - `make test` 1079 passed, 19 strict xfail (partner tools);
  - trace 154/154;
  - `app/policy` + `state.py` branch coverage 100%;
  - mutmut gate on `app/policy`: 1836 mutants, 0 logic survivors;
  - `make fuzz` 35 operations clean;
  - Playwright 8 passed on Postgres (4 workers).

### Done (Nov 5, after the critique; local commits, not pushed)
- UI redesign (`31bb5f1`): one design system (maroon on warm neutrals, serif headings, Lucide
  icons, no dot pills); a landing page with an animated isometric "how it works" scene and a
  system diagram; Console-style Control Room with a sidebar; quieter case, buyer, seal, shop and
  verify pages. Every locked selector kept.
- SL-07 and SL-08 (`b863033`, ADR-042): a seal scan from the seller's own browser is recorded but never
  counts. Signed HttpOnly cookie set by the Control Room and the seal card; the seal page and the
  seller timeline say why a scan did or did not count.
- PP-11 (`c960ba6`, ADR-043): PayPal's own Seller Protection verdict on each capture is read
  from the capture and the webhook, kept in the audit trail and shown to the seller. It is not
  evidence, so signals and the pre-refund fingerprint never change because of it. Not yet seen
  on a live sandbox capture (no real capture in the local data); the field comes from the SDK
  model.
- EX-05 (`8d29d41`, ADR-044): every runtime model call is bounded at 20 s with no hidden SDK
  retries. A buyer's tap waits on at most two bounded calls, then the template (was about 3 min
  in the worst case).
- FE-09 (`d86714b`): the 60-case eval set and a 20-case hold-out, labels from SPEC section 7
  written before the engine ran. 96.7% (58/60), hold-out 95% (19/20); all misses are low
  confidence, so they go to a person (`evals/report.md`).
- Docs (`38cea40`): README and SUBMISSION rewritten around the liability router, every number
  re-measured, unbuilt tools removed from the claims, a proposed business model added. The
  unsourced "31%" in the video script is replaced by the sourced 43%.
- Secret scan of all 66 commits (pattern-based; gitleaks is not installed locally, CI runs it):
  no real keys. The only key-shaped values are the made-up fixtures in `test_se_secrets.py`.
  `.env` was never committed.
- Checks: `make lint` clean; `make test` 1112 passed, 19 strict xfail; trace 158/158; policy +
  state branch coverage 100%; `make fuzz` clean; Playwright 8/8 locally.

### In progress
- Release (Nov 5): pushed and deployed `1c6b1e9` to https://doorstep-ojmg.onrender.com.
  - `make smoke` OK against the PayPal sandbox: real capture, tracking, refund
    `9T338104T05821332`, receipt chain ok.
  - Playwright 8/8 against the live app. Expect waits are now 20 s, because the live
    report runs the explainer model inline (5 to 7 s).
  - CI: the gitleaks job flagged two made-up test secrets. They are now ignored by exact
    fingerprint (ADR-041); CI is green.
  - `/paypal:doctor sandbox`: 0 critical, 0 warnings. The PayPal sandbox MCP server still fails
    with "Unsupported cache mode", a plugin bug; the REST path is proven by the smoke test.
- Fixed (Nov 5, Render env): live claim packs stayed as template text. The worker had its own
  `LLM_PROVIDER=gemini` row, which overrode the shared group's `anthropic`, and Gemini was
  returning 503/429. With `anthropic` set on the worker, the pack is redrafted by the model in
  about 10 s (8 of 8 sentences verified). The worker's own PayPal client id and secret were
  from a different app than `doorstep-shared`. The human removed the worker's duplicate rows and
  redeployed. After that, on the live app:
  - the claim pack is model text about 5 s after the report;
  - the refund job runs in about 5 s and dispute evidence in about 10 s (it took 45 s while
    slow Gemini failures held up the queue);
  - Playwright 8/8.
  - Not yet tested live: a real sandbox refund through the worker. That needs a PayPal popup
    checkout; do it while filming.
- Waiting on the human:
  - `tests-locked-core` is still at 48f38bd (local and origin), so `make locked` fails;
  - lock-tag move to the addendum commit.

### Not started (day-1 definition of done)
- [ ] Sandbox order with shipping options → capture → tracking → partial refund → sandbox dispute settled
- [ ] Shippo test webhook received
- [ ] Repo, Render blueprint and CI skeleton live

### Notes
- FE-09 dataset and PayPal webhook handlers are built (see Nov 5 entries).
- PayPal sandbox MCP fails on every tool with `PAYPAL_API_SETUP_ERROR: Unsupported cache mode:
  default`. This is a server-side bug at PayPal; the token is valid via REST. Report it upstream.
- `PAYPAL_WEBHOOK_ID` is not set yet; webhook verification can't be tested live until it is.
- Phase D must add a contract test: fake PayPal responses match recorded sandbox shapes.
