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

## ADR-014 — PayPal Python SDK: APIMatic context-plugins git SDK, pinned to a commit SHA

- **Date:** 2026-11-04 · **Status:** accepted (human-approved rule: PyPI if the APIMatic skill
  guidance applies to it, otherwise git pinned to a SHA)
- **Context:** CLAUDE.md says to use the PayPal Server SDK for Orders/Payments, guided by the APIMatic
  Context Plugin. There are two candidates:
  - PyPI `paypal-server-sdk` 2.4.0 (2026-08-21): import root `paypalserversdk`, built on
    apimatic-core. It ships no `sdk-map.md` and no `map/operations/`.
  - `github.com/context-plugins/paypal-python-sdk`: HEAD `143e57f2a3729d7df3727386b8c9ddf023d9a5df`
    (2026-10-13), dist `paypal` 2.29, import root `paypal`, Python >=3.10. Deps: httpx2
    (pydantic/httpx2), pydantic 2, certifi, typing-extensions. It ships `sdk-map.md` and
    `map/operations/` (orders, payments, subscriptions, transaction_search, vault).
  The APIMatic `python-*` skills need import root `paypal` and the SDK map at the SDK root. That
  matches only the git SDK.
- **Decision:**
  - Use `paypal @ git+https://github.com/context-plugins/paypal-python-sdk.git@143e57f2a3729d7df3727386b8c9ddf023d9a5df`.
    Never pin to `@main`; changing the SHA needs a PR.
  - The dependency is added in phase C, when the first SDK code is written.
  - SDK code follows the APIMatic workflow: `paypal-plan.md` at the repo root (contract sheet), SDK-map
    lookups, no facts from memory.
  - Tracking, disputes and webhook verification stay REST (`app/paypal_client`).
- **Trade-off:**
  - The SDK comes from a beta code generator, the repo has only 4 commits and no tags, and the
    package is published by APIMatic, not PayPal.
  - Its dist name `paypal` collides with an unrelated old PyPI package (`paypal` 1.2.5). Only the
    git URL may be used.
  - Tests fake PayPal over HTTP (ADR-013), so falling back to PyPI 2.4.0 (`paypalserversdk`)
    changes only `app/paypal_client` internals.

## ADR-015 — SDK models, our own transport

- **Date:** 2026-11-04 · **Status:** accepted (phase C; see paypal-plan.md)
- **Context:** The pinned SDK (ADR-014) has its own transport, but:
  - its token cache uses `time.monotonic`, not our injectable Clock;
  - it does not retry a 401;
  - SDK retries are on by default (we retry only in the outbox);
  - `patch_order` and `create_order_tracking` take no `PayPal-Request-Id`;
  - its typed error model needs `message` + `debug_id`, so a thin error body fails to decode and
    the status is lost.
- **Decision:** Use SDK models (`OrderRequest`, `Patch`, `OrderTrackerRequest`, `RefundRequest`,
  `Order`, `Refund`) to build request bodies (`to_dict`) and decode responses. Transport, OAuth,
  401 refresh-once, logging, `debug_id` capture and error mapping stay in `app/paypal_client/client.py`
  (httpx).
- **Trade-off:** We don't use the SDK's call surface. The models still keep wire shapes right.
  Phase D: the real sandbox 422 body did include `message`/`debug_id`
  (`recordings/paypal/capture_declined.json`), so the decode concern is about thin/5xx bodies only.
  Re-check before switching to the SDK transport.

## ADR-016 — Refund ceiling and pre-refund re-check

- **Date:** 2026-11-04 · **Status:** accepted (phase C, PO-06/PO-12)
- **Decision:**
  - A refund amount is clamped to the remaining refundable amount: order amount minus queued,
    completed and external refunds.
  - Right before the PayPal call, `RefundJobHandler` re-checks:
    - no open dispute;
    - the evidence fingerprint is unchanged;
    - the live capture is `COMPLETED` in the same currency;
    - the live refunded total equals our known total, or known + this refund (an earlier attempt
      that landed);
    - known + this refund ≤ min(live capture, order amount).
  - Any mismatch → money action `BLOCKED`, case → `HOLD_FOR_REVIEW`.
- **Trade-off:** It's a ceiling, not strict equality with the capture, so an order captured above
  its stored amount still refunds (capped at the order amount). PayPal `PENDING` refunds count as
  refunded.

## ADR-017 — Repeat claimants and the daily cap

- **Date:** 2026-11-04 · **Status:** accepted (phase C, PO-04/PO-08)
- **Decision:**
  - **Repeat claimant:** count this seller's cases for the same PayPal payer opened in the last
    `window_days` (90), plus this case. At ≥ `threshold` (3) → hold. No payer id → not counted.
  - **Daily cap:** sum this seller's queued + completed, non-external refunds in this currency
    since 00:00 UTC.
  - External refunds (made outside Doorstep) count toward the refundable ceiling, but not the cap.
- **Trade-off:** The window uses case open time, not order time. UTC day, not the seller's local day.

## ADR-018 — Failure-point engine: fallback and contradiction

- **Date:** 2026-11-04 · **Status:** accepted (phase C, FE-01..FE-08)
- **Decision:**
  - Candidates are ranked by `FAILURE_POINT_PRIORITY`. Score = support weights − missing-signal
    penalty − ambiguity penalty; thresholds in `tuning/engine_config.py`.
  - No matching candidate → `FALLBACK_BY_CLAIM` at LOW confidence (→ human).
  - CONTRADICTS also fires when:
    - a WRONG_ITEM claim meets a packing-photo match, or a cleared mismatch;
    - a DAMAGED claim meets a buyer photo showing no damage.
- **Trade-off:** Weights are hand-set until the FE-09 dataset and autoresearch exist.

## ADR-019 — Carrier mapping and phase-D items to verify

- **Date:** 2026-11-04 · **Status:** accepted (phase C)
- **Decision:**
  - **Shippo status → order state:**
    - PRE_TRANSIT → SHIPPED;
    - TRANSIT → IN_TRANSIT;
    - DELIVERED → DELIVERED;
    - FAILURE/RETURNED → EXCEPTION;
    - UNKNOWN → no-op.
  - Illegal or backward events are recorded and ignored (SM-09). There is no implicit
    PAID → SHIPPED; a label purchase does that.
  - Carriers PayPal doesn't know are sent as `OTHER` + `carrier_name_other`. Locker = PayPal
    shipping option type `PICKUP`.
- **To verify in the sandbox (phase D):**
  - whether shipping options need an amount breakdown;
  - the PATCH path `/purchase_units/@reference_id=='default'/shipping/address`;
  - `admin_area_1` rules for US addresses;
  - real webhook payload shapes for capture, refund and dispute events.

## ADR-020 — Deterministic test gate; exploration is separate

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** `test_SM_04_no_refund_without_paid_property` passed or failed depending on
  Hypothesis's random examples. `approve` before `decide` raised `PolicyError`, which the
  property tolerates only as `IllegalTransition`. A gate that depends on luck is not a gate.
- **Decision:**
  - The wrong-state errors from `approve` and `decline` are `PolicyStateError`, a subclass of
    both `PolicyError` and `IllegalTransition`: no decision, wrong case state, no completed
    capture, refund already approved.
  - `make test` passes a fixed `--hypothesis-seed` (Makefile `HYPOTHESIS_SEED`, default
    20261104). It runs the same examples every time.
  - `make test-explore` runs the property suite `EXPLORE_RUNS` times (default 20), each with
    a fresh random seed. It is not a gate. A failure prints the seed, so it can be replayed
    with `make test HYPOTHESIS_SEED=<seed>` and pinned as an `@example` in a reviewed test.
  - mutmut (`make mutmut`, `[tool.mutmut]`) uses the same fixed seed.
- **Trade-off:** The gate sees a fixed sample. New counterexamples come only from exploration
  runs, so someone must run `make test-explore` (CI can run it nightly). A local
  `.hypothesis/` database can still replay a saved failure; CI starts without one.

## ADR-021 — Refund jobs: finished cases, in-flight declines, missing rows

- **Date:** 2026-11-04 · **Status:** accepted (human-approved; bugs found by the coverage
  addendum)
- **Decision:**
  - **Decline:** `decline` is refused (`PolicyStateError`) once the latest decision has a
    refund money action. Money already in flight is never orphaned by a decline.
  - **Re-check:** the pre-refund re-check first requires the case to be unfinished. A CLOSED
    or SETTLED case never sends money: the money action becomes BLOCKED and the case stays
    as it is.
  - **Missing rows:** a refund job whose money action, order, case or decision is missing
    raises `MissingRecord`, a `PermanentError`. The job fails on its first attempt, and the
    runner writes the `job.failed` audit row. `on_failed` then:
    - marks the money action FAILED;
    - logs at ERROR;
    - holds the case if it still exists.
    If the money action itself is gone, the job is an orphan: logged at ERROR and listed by
    `app.worker.orphans.orphaned_refund_jobs` for the Control Room. The job payload holds
    only `money_action_id` (pinned by PO-02), so no case can be held for an orphan.
  - **Worker:** the runner runs `on_failed` inside a savepoint and catches its exceptions,
    and `run_due` catches per-job exceptions. One bad job never stops the loop.
- **Trade-off:** A broken `on_failed` hook is logged, not retried. Its job is still marked
  failed with an audit row.

## ADR-022 — Refund identity: our money_action id in PayPal's custom_id

- **Date:** 2026-11-04 · **Status:** accepted (human-approved: option b with a safe fallback)
- **Context:** If PayPal applies a refund but our commit is lost, the job is retried. ADR-016's
  worst-case ceiling can't tell our landed refund from an unknown one of the same amount. So a
  full refund in that state is always blocked, and PO-07's "retry with the same key" never
  settles it.
- **Decision:**
  - Every refund request carries `custom_id = money_action.id`. Every retry reuses the same
    `PayPal-Request-Id` and the same `custom_id`.
  - `get_capture_state` also returns the capture's refunds: id, status, amount, custom_id.
  - Crash recovery, before any send:
    - a refund with our `custom_id` and the same amount is ours: record it COMPLETED, write
      `paypal_response.recovered = true` and settle the case, with no resend;
    - a refund with our `custom_id` but another amount, without a `custom_id`, or with
      someone else's `custom_id`, can't be identified: BLOCKED, HOLD_FOR_REVIEW (ADR-016);
    - an unidentified refund blocks even when a resend would fit under the ceiling.
- **Phase D:** in the live sandbox, check that `custom_id` comes back when reading refunds
  (GET order → `purchase_units[].payments.refunds[]`). If it doesn't, fall back to option (a)
  (always block on an unexplained refunded amount) and record that here.
- **Trade-off:** One more field on every refund, and recovery depends on PayPal echoing
  `custom_id`. The locked `test_PO_07_crash_after_paypal_success_retries_with_same_key` fakes
  a landed refund without `custom_id`, so under this ADR it is blocked. The test still passes,
  since it asserts no double refund and the same request id.

## ADR-023 — Reason codes, and a mutation gate on the money path

- **Date:** 2026-11-04 · **Status:** accepted (human-approved)
- **Context:** mutmut on `app/policy` left 356 survivors. Most only changed human sentences, so
  tests could not tell "the reason changed" from "the wording changed", and real logic gaps
  were hidden among them.
- **Decision:**
  - **Reason codes:** policy decisions and blocked refunds carry machine codes
    (`app/policy/reasons.py`, `Reason` StrEnum). `PolicyDecision.codes` and
    `decisions.reason_codes` hold every code in order. The case transition reason and the
    blocked money action (`paypal_response.blocked_code`) carry the final code. Tests assert
    codes. The sentences live only in `MESSAGES`, marked `# pragma: no mutate`.
  - **Gate:** zero surviving logic mutants in `app/policy`. `make mutmut-gate` runs mutmut,
    then `scripts/mutmut_gate.py`, which compares each surviving mutant's function with the
    original as an AST. A survivor is text-only (allowed) only if the difference is inside a
    logger call, a raised exception's arguments, or the placeholder values of `message()`,
    `note()` or `_Verdict.block()`. Anything else, including a changed string in a
    comparison or an audit key, fails. Crashed or unchecked mutants also fail.
  - **Equivalents:** a mutant that can't change behaviour is excluded at the source with
    `# pragma: no mutate` and listed in `docs/mutation-report.md` with a one-line reason. A
    pragma without an entry there is a review failure.
  - **CI:** `.github/workflows/mutation.yml` runs `make mutmut-gate` on ubuntu-latest when
    `app/policy/**` (or the gate) changes.
- **Trade-off:** A full mutation run takes minutes, so it only runs when the money path
  changes. The text-only rule is narrow on purpose; a new text-only position (e.g. a new
  helper) must be added to the gate in review, not guessed.

## ADR-024 — Signed links instead of accounts; tables created at startup

**Context.** Buyers and sellers must act with zero typing (SPEC section 4, principle 6), and
judges need a no-login path (SPEC section 15). There is no migration history yet.
**Decision.** Buyers act through an HMAC-signed, order-scoped link (scope `buyer`); the seller
through a signed seller link (scope `seller`, subject = seller id); MCP gets scope `mcp`. A bad,
expired or wrong-scope token, or an id outside its scope, answers 404 (BC-01, BC-04).
`Base.metadata.create_all` runs at startup; no Alembic in the hackathon build.
**Trade-off.** A leaked link grants that one order's actions until it expires (30 days); links
are not revocable without rotating `SIGNING_SECRET`. Adding columns later needs a fresh database
or a first migration.

## ADR-025 — One model adapter on the official SDK; no temperature on Claude Sonnet 5.5

**Context.** CLAUDE.md asks for temperature 0 on checks. Claude Sonnet 5.5 returns 400 for a
non-default `temperature` and for forced `tool_choice`.
**Decision.** `app/ai/llm.py` is the only module that talks to a model. Claude goes through the
official `anthropic` SDK with `messages.parse(output_format=<Pydantic model>)` (structured
outputs) and `effort: low`; Gemini goes over REST with `responseJsonSchema` and temperature 0.
Invalid output: one retry on the same provider, then the caller's template fallback. Budget,
quota, auth or outage on the main provider: one retry on the fallback provider; the answering
provider is stored with the explanation. User text is redacted (emails, phones, postcodes to
district) before any call (SE-02). Opus model ids are refused.
**Trade-off.** Claude outputs are schema-guaranteed but not sampling-deterministic; evals record
and replay outputs instead of relying on temperature.

## ADR-026 — Signed receipts on one global hash chain

**Context.** PR-01..03 ask for tamper-evident money decisions anyone can check.
**Decision.** Every decision, approval, decline and completed refund appends an Ed25519-signed
receipt whose hash covers the previous receipt's hash. `GET /cases/{id}/verify` recomputes the
chain from receipt 1 up to that case's last receipt and names the first broken one. Payloads hold
ids, amounts and rule ids only, because the endpoint is public. `/healthz` publishes the key.
**Trade-off.** Verification cost grows with the whole chain (fine at hackathon scale; a
per-period checkpoint would bound it). Demo reset wipes the chain, so reset is development-only.

## ADR-027 — Seeded scratch data uses simulated refunds, labelled as such

**Context.** Judges each get an isolated scenario (PR-06), but seeded orders never went through a
PayPal checkout, so they have no real capture to refund.
**Decision.** Seeded orders carry capture ids starting `SIM-`. The worker's refund gateway routes
those to `SimulatedRefundGateway` and everything else to PayPal. The full policy path still runs
(limits, caps, approvals, pre-refund re-check, receipts); only the final PayPal call is
simulated, and its status reads `simulated` everywhere (PR-08). `/healthz` lists it. The filmed
refund comes from a real sandbox checkout.
**Trade-off.** Scratch refunds prove the decision path, not PayPal's refund API; the sandbox
smoke test (E2E-03) and the filmed demo cover the real call.
