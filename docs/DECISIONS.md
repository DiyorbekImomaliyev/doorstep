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

## ADR-028 — Render blueprint: web + worker + Postgres; the worker also sweeps

**Context.** SPEC section 11 asks for a one-click deploy with a web service, worker, cron job and
Postgres. Receipts and links must verify in every process, and secrets must never be in the repo.
**Decision.** `render.yaml` defines `doorstep` (FastAPI, also serves the built React app),
`doorstep-worker` (inbox, jobs, and the stale-tracking sweep every 5 minutes, so no separate
cron service) and `doorstep-db`. A shared env group generates `SIGNING_SECRET` and
`RECEIPT_SIGNING_KEY` once; any secret that is not a raw seed is stretched with SHA-256, so both
processes derive the same Ed25519 key. API keys are `sync: false` (typed in the dashboard).
`EMBEDDED_WORKER=1` runs the worker loop inside the web process for a one-service deploy.
In development, the receipt key is stored beside the local SQLite file (gitignored) so stored
receipts keep verifying across restarts. `POST /demo/seed?scratch=0` (the filmed demo) is
development-only; `make demo` prints the filmed links privately.
**Trade-off.** Render workers have no free plan (starter instances cost money); the embedded
option avoids that at the cost of sharing one process. Postgres tables are created at startup
(ADR-024), not migrated.

## ADR-029 — Packing check: the model describes, code decides, never auto-pass

**Context.** PK-01..04 and SPEC section 10: one photo against the order line before shipping;
vision can misread, and a false "match" would hide a wrong item.
**Decision.** `app/ai/packing.py` asks the configured provider (via `app/ai/llm.py`) for a
`PackingCheckResult` at temperature 0, with photo text treated as data. Code turns it into a
verdict: confident + matches → match; confident + not matching + a named attribute → mismatch;
everything else (low confidence, no attribute, invalid output twice, provider down, no model) →
"check". The threshold and the prompt live in `tuning/engine_config.py`. `PackingService`
stores only the photo's SHA-256 with the verdict; a match moves PAID → PACKED, and a flagged
check needs the seller's "I checked it" (recorded as `cleared`, read by the engine). Photos are
shrunk to 1600 px JPEG in the browser; the API caps uploads at 5 MB.
**Trade-off.** No photo bytes are kept, so a later dispute cannot show the packing photo itself;
storing it needs object storage and a 90-day deletion job (SPEC section 13), left for later.

## ADR-030 — MCP tool: one order per token, opens a case, no money tools

**Context.** MC-01..03 and SPEC section 9: buyers' AI agents should get the same fair process
through an MCP tool, without any way to move money or see other orders.
**Decision.** `app/mcp/server.py` (FastMCP) exposes `get_delivery_status` and
`report_delivery_problem`. Both take an `order_token` signed with the `mcp` scope (buyer links
are refused); no tool takes an order, seller or case id. The order work is injected as
`AgentPort` (adapter in `app/api/agent.py`), so `app.mcp` never imports the pipeline or money
code; import-linter checks it. A report runs the same pipeline as "Not here", recorded as
`reported_by=agent`, so the coded policy decides the outcome. The buyer page mints a 7-day agent
token. The server is mounted at `/agent/mcp` (streamable HTTP, stateless, JSON responses) and
its lifespan runs inside the API's.
**Trade-off.** Stateless mode keeps deploys simple but has no server-to-client notifications;
the agent polls `get_delivery_status`. A report under the auto-approve limit can still end in
an automatic refund, exactly as the buyer's own tap would: the policy decides, not the agent.

## ADR-031 — Case agent: Agent Toolkit reads, one policy-gated money tool, our model adapter

**Context.** AT-01..05 and the agentic-commerce track: an agent that acts, inside the seller's
mandate. CLAUDE.md asks for the PayPal Agent Toolkit (LangChain adapter with Claude) with an
allowlist of orders get, shipment create/show, disputes list/get. Two hard rules conflict with a
literal reading: every model call goes through `app/ai/llm.py`, and every PayPal write goes
through the jobs outbox with a `PayPal-Request-Id` (hard rule 4).
**Decision.** `app/agent/toolkit.py` builds the toolkit's LangChain tools with an explicit
allowlist of *reads* only (orders get, shipment get, disputes list/get) and refuses any other
tool. Shipment *create* is left off: tracking is already registered by the outbox job (TR-05),
with idempotency, so the agent never writes to PayPal directly. The planner is not LangChain's
chat model: each step is an `AgentStep` (Pydantic) from the configured provider through
`app/ai/llm.py`; with no model, or after a failed retry, a fixed plan runs the same steps. The
model never chooses ids (code fills them from the case's order; a dispute must be one PayPal
listed), steps are capped, and tool answers are reduced to short summaries with no personal
data. The only money tool, `request_refund`, is `app/policy/agent_gate.py`: it reports what the
policy decided (refund queued under the limit, escalated, held, dispute path) and creates
nothing. Each step writes an audit row shown in the seller's case timeline. Seeded orders use
simulated reads in the toolkit's shape, labelled simulated. Import-linter now covers `app.agent`.
**Trade-off.** The agent cannot create shipment tracking itself, and it never adds a refund the
policy did not already decide; its value is evidence gathering, a clear trail, and a safe
"ask, then stop" at the seller's limit.

## ADR-032 — Risk model: XGBoost with built-in TreeSHAP, trained on synthetic data

**Context.** SPEC section 10 asks for a calibrated XGBoost + SHAP delivery-risk model (RK-01..03).
We have no real outcomes yet. Render's free tier makes a large build costly: the default Linux
`xgboost` wheel pulls CUDA/NCCL, and `shap` pulls numba and llvmlite.
**Decision.**
- Packages: `xgboost-cpu` on Linux (about 6 MB) and `xgboost` elsewhere.
- Reasons: exact Shapley values from XGBoost's own `pred_contribs`, with no `shap` package.
- Calibration: Platt scaling written in numpy, so no scikit-learn.
- Data: synthetic, from a stated ground truth seeded from published rates (`TRUTH` in
  `app/ai/risk_train.py`).
- Constraints: monotone, so a locker never looks riskier and a pricier parcel never looks safer.
- The model is committed and deterministic. A test retrains it and fails if
  `tuning/engine_config.py` changed without `make risk-model`.
- Checkout scores doorstep delivery with the model and falls back to the rule of thumb if the
  model is missing.
- No postcode or address features.
**Trade-off.** Accuracy is only proven on the synthetic world: PR-AUC 0.155 against a 0.156
ceiling. Real effect sizes need real seller outcomes. The model card states this.

## ADR-033 — Policy reader: BM25 over sentence passages, cited answers, rule-file fallback

**Context.** SPEC section 10 plans a pgvector RAG over seller policies and rule texts (RG-01..03).
A seller's corpus is a few sentences of written policy plus the `policy_text` of the rule set
that applies. Embeddings would add a model call per passage, a pgvector-only code path that
SQLite tests can't cover, and nondeterminism. None of that buys recall on a corpus this small.
**Decision.**
- Passages: sentences with stable ids (`seller:1`, `us_seller_policy_v1:2`).
- Retrieval: BM25 (`RAG_TOP_K` and `RAG_MIN_SCORE` in tuning/engine_config.py).
- Answer: one structured `PolicyAnswer` through app/ai/llm.py at temperature 0. It must cite at
  least one retrieved id. A citation outside the retrieved set counts as invalid output and gets
  one retry.
- Fallbacks:
  - no model, or invalid twice: the passages are quoted verbatim with their ids;
  - no relevant passage: the rule file itself is quoted, with id, version, source and outcome
    (RG-02).
- Use in the app: the seller's case drawer shows "What your policy says" on demand. It is never
  read by a decision.
**Trade-off.** Lexical retrieval misses paraphrases with no shared words, e.g. "parcel never
came" vs "does not reach you". The rule-file fallback keeps that case safe. pgvector embeddings
are the upgrade once policies grow past a page. RG-03 (Ragas faithfulness on 30 questions) needs
a run on the eval model key and stays xfail until then.

## ADR-034 — A PayPal 429 is retried, not treated as a permanent 4xx

**Context.** `/paypal:doctor` (Nov 5) flagged that the outbox marked every `PayPalClientError`
permanent. That included 429 RATE_LIMIT_REACHED. Under a burst, an auto-refund under the limit
would fail at once and go to `HOLD_FOR_REVIEW`. That is safe, but it means needless manual work.
**Decision.**
- `app/worker/runner.is_permanent` treats 429 as retryable. The job's existing backoff and
  `max_attempts` apply, and the retry uses the same `PayPal-Request-Id`.
- Every other 4xx still fails at once.
- Tests: `tests/unit/test_pp_rate_limit.py`. It checks the classification table, and that a
  refund rate-limited twice settles with one request id and one refunded amount. It also checks
  that a tracking job survives a 429.
**Trade-off.** Retries can arrive sooner than PayPal's `Retry-After` header asks, because the
job backoff ignores that header. With at most `max_attempts` tries this is harmless here.

## ADR-035 — Possession seal: the buyer's own phone proves possession

**Context.** CRA 2015 s.29 (and EU Art. 20) puts the risk on the seller until the buyer has
*physical possession*. The proofs we had (signature, locker code, a carrier photo) cost money or
depend on the carrier. A "Delivered" scan proves nothing about possession. The research round
(Nov 5) found no cheap, carrier-independent possession proof for small sellers.
**Decision.**
- A QR code goes on the packing slip inside the parcel. It holds a signed token
  (`LinkSigner`, scope `seal`, 180 days) for one order. The same URL can be written to an NFC
  sticker, which phones open natively, so it needs no code.
- The seal page offers "I have it" and "Something's wrong". There is no login, no typing and no
  location.
- A scan counts as `buyer_seal_scan` possession proof only if a carrier transit, delivered,
  failure or returned event exists at or before it. This stops a seller pre-scanning while
  packing. Scans before shipping are refused.
- The UK, EU and US rule files list the proof (rule versions bumped to 2026-11-06). A counted
  scan followed by "Not here" goes to the seller (FE-11), never to an automatic denial.
**Trade-off.** A dishonest buyer can simply not scan. Not scanning is never evidence against
anyone, so the seal helps honest sellers against later "not received" claims and gives honest
buyers the fastest path for damaged or wrong items. Scan rates are unknown and will be measured,
not assumed.

## ADR-036 — Delivered-scan audit is deterministic code

**Context.** Carriers deny claims and sellers lose arguments because the "Delivered" scan is
treated as fact. Couriers under time pressure scan early, and drivers deviate from routes. The
carrier's own event data (Shippo `tracking_status.location`, event order, timestamps across a
seller's parcels) can contradict the scan.
**Decision.**
- `app/cases/scan_audit.py` is a pure function. It runs three contradiction checks (location
  district or ZIP5, impossible sequence, batch scan) and one context-only check (reported soon).
- Verdicts: contradicted, consistent, unverified, no_scan.
- Unknown inputs never count as a contradiction. The Shippo test carrier's sample locations count
  as unknown (PR-08).
- Thresholds live in `tuning/engine_config.py`.
- A rule can set `contradicted_scan_counts_as_proof: false` (the US seller policy). Then a
  contradicted scan is not proof of delivery, and the decision records `SCAN_CONTRADICTED`.
**Trade-off.** We chose rules over an anomaly model. Without real carrier data, a model would be
trained on synthetic data, and carrier claims need findings a person can read and check.
Location precision depends on the carrier (city or postcode, not GPS).
**Built (Nov 5).**
- The batch check counts only parcels with the **same carrier**, because a batch scan is one
  carrier's behaviour. For the Shippo test carrier it reports unknown, as location does, because
  test-mode scan times are sample data and would flag every demo order.
- A scan dated up to `SCAN_AUDIT_FUTURE_SKEW_S` (15 min) after the report is clock skew, not a
  scan from the future.
- A contradicted scan is also a failure-point engine signal (`scan_contradicted` → CARRIER_LOST).
  The signal is off by default, so the existing engine tests and the eval set are unchanged.
- The carrier's doorstep photo now also gives `delivery_photo`. Before this, the US rule's
  `proof_required: [delivered_scan, delivery_photo]` could never be met through the pipeline.
  A US case with a photo whose scan is contradicted gets two engine candidates (stolen after
  delivery, carrier lost) and goes to the seller. It is never decided automatically.

## ADR-037 — Carrier terms in rules/carriers/, recovery as its own small state machine

**Context.** Locked tests RL-01 and RL-09 pin the core rule book to five files. Carrier claim
windows and standard cover are business rules (hard rule 2), but they are not liability rules.
**Decision.**
- `rules/carriers/carrier_claims_v1.yaml` has its own strict loader
  (`app/policy/carriers.py`): extra keys forbidden, quoted version, id equals file name, a
  source per carrier. Startup fails if the file is invalid. A `default` entry covers unknown
  carriers and tells the seller to check their carrier's terms.
- A `recoveries` table holds one row per case. Its transition table is PREPARED → SUBMITTED |
  WITHDRAWN | EXPIRED and SUBMITTED → PAID | DENIED, with an audit row per change.
- Claim packs are verified in code. Each sentence must cite fact ids built by code, and any
  number in it must appear in those facts. A failing sentence is dropped. If too few survive, a
  template built from the facts is used.
**Trade-off.** Small sellers have no carrier claims API, so Doorstep prepares and tracks the
claim and the seller submits it on the carrier's site. The order and case state machines stay
untouched.
**Built (Nov 5).**
- The carrier file is loaded by `load_rules` and attached as `RuleBook.carriers`. It is
  validated wherever the rule book is: the web app, the worker and the scripts. It is not in
  `rule_ids`, so the locked RL-01/RL-09 tests are unchanged.
- Values were checked on each carrier's own pages on 2026-11-05: Royal Mail (Tracked 24/48:
  £150, 30 days), Evri (£20 basic cover, 28 days), USPS (DMM 609: $100 included, 60 days), UPS
  (2026 tariff: $100, notice within 60 days of delivery) and FedEx ($100; 60 days for damage,
  9 months for loss). Where a carrier publishes several windows, the shortest for the named
  service is used.
- DPD UK uses `default`. Only third-party pages were found for it, and they disagree, so we do
  not guess (CLAUDE.md: do not guess).
- A deadline that runs from delivery falls back to posting when there is no delivered scan. That
  is earlier, so it is never late.
- The template pack is written at once. The AI wording runs later in the worker
  (`draft_recovery` job), so the buyer's "Not here" never waits on a model. A retried job does
  not call the model twice.
- Number check: tokens are normalised, so "£60" matches "£60.00" and "9:14" matches "09:14".
  A sentence with any other number is dropped. Accusing words are dropped too (EX-02 guard).
- US facts use the ZIP5, the US equivalent of a postcode district (hard rule 9). Facts never
  hold the street address, the name or the buyer's free text.

## ADR-038 — Dispute evidence goes through the policy outbox

**Context.** Cases reached `DISPUTE_EVIDENCE`, but nothing called
`provide_dispute_evidence`. The PayPal dispute, where PayPal is the judge in the middle, never
saw our evidence.
**Decision.**
- The seller action `send-evidence` makes the policy service create one
  `MoneyAction(kind=evidence)`. The existing unique (decision_id, kind) constraint stops double
  sends.
- A `paypal.dispute_evidence` job carrying a `PayPal-Request-Id` calls provide-evidence through
  the policy gateway (`PROOF_OF_FULFILLMENT`, carrier and tracking number).
- Notes hold facts only and are checked against the explainer's accusing-words guard.
- `SIM-` captures use the simulated gateway. A signed receipt records the submission.
**Trade-off.** Evidence is sent only on the seller's tap, never automatically. That is slower,
but a person always decides what goes to PayPal about a buyer.
**Built (Nov 5).**
- `app/policy/evidence.py` holds the money action, the job (`paypal.dispute_evidence`) and its
  handler. The notes are built in `app/cases/dispute_evidence.py` from code facts:
  - the carrier and tracking number;
  - the last 8 tracking events;
  - a counted seal scan (an uncounted or missing scan is never mentioned);
  - the scan-audit verdict;
  - the receipt verify link.
  There is no address, no buyer note and no name. Notes are capped at PayPal's 2,000 characters.
- The evidence action is a `MoneyAction` with `amount_minor = 0`, so it moves no money. The
  Control Room's refund column ignores it.
- The case stays in `DISPUTE_EVIDENCE` after sending. PayPal decides the dispute, and its
  `CUSTOMER.DISPUTE.RESOLVED` webhook is the outcome; Doorstep does not settle the case.
- The handler checks again at send time. If the dispute is no longer open, the action is
  `BLOCKED` and nothing is sent. A 4xx marks it `FAILED`; 429 and 5xx retry with the same
  request id.
- Two receipts are signed: `send-evidence` (the seller's tap) and `evidence` (what PayPal
  received, with the SHA-256 of the exact notes).
- PayPal carrier codes are mapped for Royal Mail, USPS, UPS and FedEx. Any other carrier is
  sent with no carrier code, as `OTHER`, so we never guess a code.

## ADR-039 — Hardware and ML options we did not build

- **GPS or BLE tracker per parcel:** rejected. A location at the address is not possession, and
  porch theft happens at the address. Trackers also cost money per parcel, need batteries in the
  post, need returns logistics and raise privacy issues. Any future tracker feed can arrive as
  another evidence kind.
- **Phone GPS on the seal page:** rejected. It adds privacy cost and little proof, because the scan
  already needs the opened parcel.
- **Anomaly-detection model on carrier scans:** rejected for now (ADR-036).
- **Doorbell-camera frames as evidence:** next, not built.
- **Vision damage check on the seal page photo:** planned last and cut first (the
  `DamageCheckResult` schema exists).

## ADR-040 — Receipt chain appends wait for each other

**Context.** The signed receipt chain is global: each receipt's `seq` is the previous one plus
one, and `seq` is unique. Two transactions deciding cases at the same moment both read the same
last receipt. The second one then failed on the unique key. The chain never forked, but the
request returned 500. Playwright seeding four demo shops in parallel on Postgres hit this.
**Decision.** `issue_receipt` first takes a transaction-scoped Postgres advisory lock
(`pg_advisory_xact_lock`). A second writer waits until the first commits, then reads the new last
receipt; READ COMMITTED gives each statement a fresh snapshot. SQLite serialises writers itself,
so it skips the lock. The unique `seq` stays as the last guard.
**Trade-off.** Chain appends are serialised across the whole app, and a long transaction (a demo
seed) holds the lock until it commits. Appends are rare and quick, so that is acceptable; a chain
per seller would remove the contention later.

## ADR-041 — Test-only signing secrets that gitleaks flags are ignored by exact fingerprint

**Context.** The CI secrets job (gitleaks) failed on the v2 push. Its `generic-api-key` rule
flagged two made-up `LinkSigner` secrets in unit tests (`test_sl_seal.py:38`,
`test_de_dispute_evidence.py:62`): their hex suffix raised the entropy over the rule's threshold.
Neither is a real key; signing keys come only from env vars (hard rule 10).
**Decision.** `.gitleaksignore` lists exactly those two fingerprints (commit, file, rule, line).
There is no path or rule-wide allowlist, so a real secret anywhere else, including later in these
two files, still fails CI. New test secrets should use low-entropy words (for example
`"test-signing-secret-0123456789"`), as the older tests do.
**Trade-off.** Each new false positive needs its own fingerprint line, which is more friction
than a path allowlist. That friction is deliberate: an allowlisted path would also hide a real
leak.

## ADR-042 — A seal scan from the seller's own browser never counts

**Context.** The critique (Nov 5) found that the seller can open the seal page for any shipped
order from the Control Room. A seller scanning their own seal after the delivered scan would have
produced a counted `buyer_seal_scan`, the strongest possession proof, against an honest buyer.

**Decision.** Opening the Control Room (`/me`) or a seal card sets a signed, HttpOnly, SameSite=Lax
cookie, scope `seller_browser`, holding the seller id (30 days). A seal scan that carries a valid
cookie for the order's own seller is recorded with `counted: false` and reason `seller_browser`;
the order transition names `seller_browser` as the actor. Nothing about the buyer's device is
stored. A later scan from another device still counts.

**Trade-off.** A determined seller can scan from a clean browser or a second phone. The cookie
closes the easy path (the seller's everyday browser and phone) and the audit trail shows who
scanned. Full protection needs a physical tamper-evident seal, which is out of scope. The order is
still confirmed by such a scan, as today, because the locked SL-02 browser test opens the seal page
in the seller's browser context; confirmation is not possession proof, so the liability decision is
unaffected. Whether a seller-browser scan should confirm at all is an open question for the human.


## ADR-043 — PayPal's Seller Protection verdict is shown, never used (PP-11)

**Context.** The critique (Nov 5) found Doorstep quoting Seller Protection rules
(`paypal_sp_v1.yaml`) without ever reading PayPal's own verdict for the payment. Every capture
(and the `PAYMENT.CAPTURE.COMPLETED` webhook) carries `seller_protection.status` (`ELIGIBLE`,
`PARTIALLY_ELIGIBLE`, `NOT_ELIGIBLE`) and the dispute categories it covers.
**Decision.** The capture job and the webhook read it (SDK field `OrdersCapture.seller_protection`)
and append each new verdict to the order's audit trail (actor `paypal`, action
`paypal.seller_protection`); a repeat writes nothing. The Control Room shows the latest verdict on
the order and case panels, labelled as PayPal's information. It is deliberately kept out of the
`evidence` table: the pre-refund fingerprint (PO-13) hashes every evidence row, so storing it there
would let a late webhook block a refund, and the engine must not treat PayPal's eligibility as
proof of delivery. No schema change (ADR-024: no migrations).
**Trade-off.** Seeded demo orders have no real capture, so they show no verdict; only orders paid
through the sandbox checkout do. The verdict is not a decision input, so a `NOT_ELIGIBLE` payment
is decided exactly like any other.

## ADR-044 — Runtime model calls are bounded at 20 seconds, no hidden SDK retries (EX-05)

**Context.** The explainer runs inline on the buyer's "Not here" request (locked tests expect the
buyer message in the response). With a 60-second client timeout, one hidden Anthropic SDK retry and
a 60-second Gemini fallback, a slow or out-of-budget provider could hold the buyer's tap for about
three minutes, and judges may test the live app.
**Decision.** `LLM_TIMEOUT_S` (default 20) bounds every runtime call on both providers, and the
Anthropic client makes no retries of its own (`max_retries=0`): the adapter's single fallback is
the only retry. A timeout raises `ProviderDown`, so the order is primary → fallback → template.
**Trade-off.** A transient 529 on Anthropic now goes straight to Gemini instead of retrying, and a
very long claim pack could hit the bound and use its template. Moving the explainer to a worker job
would remove the wait entirely but changes the locked report contract; not done in the hackathon.

**Addendum (SL-08, Nov 5).** A screenshot showed the evidence meter at 100% after the seller's own
"I have it": the scan did not count, but the confirmation row filled "possession proof". The meter
now ignores confirmations whose source is the seller's browser, and "Got it" on the buyer's
tracking link (which the seller can open from the Control Room) uses the same cookie check, so the
audit trail names the seller's browser. The order still moves to "Received" (ADR-042 trade-off).

**Addendum (strict, Nov 5, approved by the human).** The seller's own browser now gets a seller
preview on the seal page: the scan is recorded and never counted, the order is not confirmed, and
"Something's wrong" answers 403 and opens no case. "Received" is a statement about the buyer, so
only the buyer's device can make it. The locked SL-02 browser test changed in one place only: the
buyer's phone opens in its own browser context (a separate device) instead of sharing the
seller's cookies; its assertions are unchanged. `web/e2e/seal-preview.spec.ts` covers the preview.
The lock tag must move to include this change.
