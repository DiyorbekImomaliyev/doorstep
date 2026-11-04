# Doorstep — acceptance criteria (the burn-down list)

This file turns docs/SPEC.md into testable statements. Every criterion has an ID.
Every test name must contain the ID it covers (e.g. `test_PO_06_refunds_never_exceed_capture`).
`scripts/trace.py` fails CI if any ID has no test. A criterion that is not built yet gets a
test marked `xfail(reason="AC-ID: limitation ...")` — that is our list of known limits.

Numbers marked (config) are targets kept in config, not hardcoded.

## PP — PayPal client
- PP-01 The OAuth token is cached and reused until less than `token_refresh_margin_s` (config,
  default 300) remains. On a 401 the token is refreshed and the call retried once; a second 401
  raises `PayPalAuthError` (no loop).
- PP-02 `create_order` sends intent CAPTURE, amount, currency and shipping options
  Home / Locker / Signature with exactly one `selected: true` (the buyer's chosen option).
  Amounts are converted from integer minor units to an exact decimal string, never via float.
  Returns order id and approve link.
- PP-03 `capture` stores `capture_id` and the capture status. The order moves to PAID (through
  `app/orders/state.py`) only when the status is COMPLETED; PENDING or DECLINED stores the status
  and does not move to PAID. A declined capture is covered by a recorded sandbox test.
- PP-04 `add_tracking` sends carrier + tracking number. Failures are retried by the job runner
  with backoff (tested with a fake clock); after `max_attempts` (config) the job is marked
  `failed` and an audit row is written. A job is never dropped silently.
- PP-05 Every write sends `PayPal-Request-Id` = `{entity}:{id}:{action}[:{money_action_id}]`,
  within PayPal's header length limit. A retry reuses the same key; two partial refunds on one
  case get different keys. The live smoke test confirms the Server SDK sends the header.
- PP-06 Every call is logged with request id, status and latency. Captured logs never contain the
  client secret, the access token or the `Authorization` header.
- PP-07 Refund (full or partial): 4xx responses are not retried; 5xx and timeouts are retried
  with the same key up to `max_attempts` (config).
- PP-08 The client reads a capture's status and the total already refunded (used by PR-04).
- PP-09 Dispute `provide-evidence` is supported. `make-offer` is not built (xfail limitation).
- PP-10 `update_shipping_address` PATCHes the PayPal order's shipping address before capture.
  The live smoke test verifies PATCH; if PayPal behaves differently, write an ADR first.

## WH — Webhooks
- WH-01 PayPal webhook with an invalid signature → 400 and nothing stored.
- WH-02 Valid webhook → stored in `webhook_inbox`, 200 returned; the handler runs no processing,
  the worker does.
- WH-03 Duplicate `event_id` → 200 and not processed again. Enforced by the DB unique
  constraint; two concurrent deliveries of one event produce exactly one processed event.
- WH-04 Shippo webhook with a wrong URL token → 404, nothing stored; the token compare is
  constant-time.
- WH-05 Unknown event types are stored and ignored without errors.
- WH-06 PayPal's verify-signature endpoint fails or times out → 503 and nothing stored, so PayPal
  retries.
- WH-07 A processing failure is retried by the worker up to `max_attempts` (config), then the
  inbox row is marked `failed`; it is never lost. Malformed JSON or a body over
  `max_webhook_body_bytes` (config) → 400, never 500.
- WH-08 When a Shippo payload has no unique event id, `event_id` =
  `sha256(tracking_number|status|status_date)`. Verified in the live smoke test; ADR if Shippo
  differs.

## SM — Order and case state machines
- SM-01 Only the order transitions in the SPEC section 8 order transition table are allowed.
- SM-02 Any other transition (order or case) raises `IllegalTransition` and changes nothing.
- SM-03 Every transition (order or case) writes an `audit_log` row in the same DB transaction.
- SM-04 Property test: no event sequence creates a refund `money_action` for an order that never
  reached PAID.
- SM-05 Only the case transitions in the SPEC section 8 case transition table are allowed
  (OPEN → GATHERING_EVIDENCE → DECIDED → AUTO_REFUND | AWAITING_SELLER | HOLD_FOR_REVIEW |
  DISPUTE_EVIDENCE → SETTLED or CLOSED, plus the four extra transitions in ADR-005).
- SM-06 Property test: a case can never reach SETTLED without a recorded decision and an
  approval record (system under the limit with the rule id, a seller id, or a reviewer id); if
  money moved, a `money_action` with PayPal status COMPLETED must also exist.
- SM-07 Order and case stay in sync: opening a case moves the order to CASE_OPEN; the case
  reaching SETTLED or CLOSED moves the order to RESOLVED; at most one open case per order.
- SM-08 Concurrent transitions on the same order are serialised with a row lock; no lost update
  (Postgres test).
- SM-09 Carrier events: a repeat event for the current state is a logged no-op; an event that
  would move the order backwards is recorded with no transition and does not fail the webhook.

## CK — Checkout (prevent)
- CK-01 After PayPal approval and before capture, the shipping address PayPal returns is
  validated; when a missing unit/flat is flagged, the buyer sees a fix prompt.
- CK-02 The buyer can keep the original address; the explicit override is stored as a signal and
  capture proceeds.
- CK-03 Home, Locker and Signature are offered; the recommended option follows the risk rule.
- CK-04 The chosen method is stored on the order and shipment.
- CK-05 The PayPal order contains the chosen shipping option.
- CK-06 A buyer-confirmed fix is PATCHed onto the PayPal order (PP-10) before capture. Capture
  never runs on an unvalidated address unless the buyer explicitly overrode it (CK-02).

## PK — Packing check (vision)
- PK-01 Output always matches the Pydantic schema in SPEC section 10.
- PK-02 Confidence below threshold (config) → "check", never "match".
- PK-03 A mismatch names the attribute that differs (item, colour or size).
- PK-04 Invalid model output → one retry → fallback "check".
- PK-05 Eval: mismatch recall ≥ 0.90 (config) on the labelled photo set.

## TR — Tracking
- TR-01 Test tracking numbers (carrier `shippo`) register and produce events.
- TR-02 Event mapping: TRANSIT → IN_TRANSIT, DELIVERED → DELIVERED,
  FAILURE → EXCEPTION, RETURNED → EXCEPTION.
- TR-03 A FAILURE event offers the buyer a choice (rebook / locker, simulated).
- TR-04 The cron sweep flags shipments with no event for longer than a limit (config).
- TR-05 Buying a label registers tracking on the PayPal payment.

## BC — Buyer confirmation
- BC-01 Buyer pages work only through a signed link.
- BC-02 "Got it" → CONFIRMED and stored as possession proof.
- BC-03 "Not here" → a case opens with the reason.
- BC-04 A signed link for one order cannot read or act on another order.

## RL — Rules
- RL-01 Rule files are validated at startup against a Pydantic model (required: `id`, `version`,
  `source`, `region`, `applies_if`, `disclaimer` and the outcome fields); an invalid file stops
  startup.
- RL-02 Every rule has `id`, `version` and `source`. `version` is a quoted string; an unquoted
  YAML date fails validation.
- RL-03 The rule set is chosen by buyer country and buyer type (`buyer_type` defaults to
  `consumer`; the EU country list lives in the EU rule file). No matching rule set →
  HOLD_FOR_REVIEW, never a guess. Two rule sets matching the same order → startup fails.
- RL-04 UK/EU consumer, no possession proof → seller liable.
- RL-05 UK/EU, locker code used or signature present → possession proven.
- RL-06 US → the seller's structured policy fields in `us_seller_policy_v1.yaml` (e.g.
  `proof_required`) decide; the free-text policy is used only for RAG and explanations; no model
  call is part of the decision.
- RL-07 PayPal Seller Protection: a shipment delivery address that differs from the PayPal order's
  shipping address is flagged as redirected.
- RL-08 Every decision stores the rule id and version used.
- RL-09 Every rule file has at least one passing and one failing case test.
- RL-10 PayPal signature guidance: an order over the threshold in `paypal_sp_v1.yaml` gets
  signature recommended.
- RL-11 `rules/money_limits_v1.yaml` holds the default auto-approve limits and daily caps per
  currency and the repeat-claim threshold (3 claims in 90 days); per-seller overrides live in
  `sellers`. None of these values appear in `tuning/engine_config.py` or in Python code.

## FE — Failure-point engine
- FE-01 Bad address: address check failed or was overridden by the buyer, or a carrier
  address-issue event → BAD_ADDRESS.
- FE-02 Carrier lost / misdelivered: no delivered scan after the stale limit (config), or a
  delivered scan with no possession proof → CARRIER_LOST.
- FE-03 Damaged in transit: packing check matched and the buyer photo shows damage →
  DAMAGED_IN_TRANSIT.
- FE-04 Stolen after safe delivery: locker code used or signature present, and the buyer reports
  "Not here" → STOLEN_AFTER_DELIVERY.
- FE-05 Wrong item packed: packing check mismatch that was not cleared before shipping →
  WRONG_ITEM.
- FE-06 Claim contradicts evidence: e.g. "empty box" but label weight and packing photo match →
  CLAIM_CONTRADICTS_EVIDENCE.
- FE-07 Every decision has a confidence label (high / medium / low) with reasons; thresholds are
  config; when several failure points match, a fixed priority order breaks the tie; a missing
  signal lowers confidence and is never treated as a match.
- FE-08 Contradicting signals or low confidence → HOLD_FOR_REVIEW.
- FE-09 Eval: failure-point accuracy ≥ 0.85 (config) on the 60-case set; hold-out reported
  separately. `scripts/trace.py` counts tests under `evals/`.
- FE-10 The engine is deterministic code using weights from `tuning/engine_config.py`; it never
  calls a model.
- FE-11 Outcomes that would put the blame on the buyer (bad address after a buyer override,
  stolen after safe delivery) route to AWAITING_SELLER with the explanation attached; never an
  automatic denial.

## PO — Policy service (money)
- PO-01 `app/paypal_client` is split into `reads` and `writes`; only `app/policy` and
  `app/worker` may import `writes`, and only `app/policy` may import `writes.money` (refunds,
  disputes). The worker runs refund jobs by calling app/policy (`RefundJobHandler`), never the
  money-writes module (enforced by import-linter contract tests).
- PO-02 Seller liable, amount at or under the auto-approve limit, and within the daily cap →
  refund job created (case → AUTO_REFUND).
- PO-03 Over the limit → AWAITING_SELLER; the seller's tap approves. A seller decline → CLOSED
  (a human decision, not an automatic denial).
- PO-04 Buyer with 3+ claims in 90 days (from `money_limits_v1.yaml`), counted as cases opened by
  the same PayPal payer id with this seller, whatever the outcome → HOLD_FOR_REVIEW, never
  auto-deny.
- PO-05 An open PayPal dispute → DISPUTE_EVIDENCE, not a separate refund. The pre-refund
  re-check (PR-04) also blocks the refund if a dispute opened after the decision.
- PO-06 Property test: total refunds never exceed the captured amount, counting refunds made
  outside Doorstep (from `PAYMENT.CAPTURE.REFUNDED` webhooks).
- PO-07 Approving the same case twice, one after the other or at the same moment → exactly one
  refund (DB unique constraint on case + action).
- PO-08 Daily refund cap per seller and per currency (UTC day; queued and completed refunds both
  count) is enforced; hitting the cap → AWAITING_SELLER, never dropped.
- PO-09 Every money action writes an audit record with signals, rule id + version, model outputs,
  approver and PayPal response.
- PO-10 All money is integer minor units plus an ISO 4217 currency code (no floats anywhere in
  the money path). Only GBP and USD are supported; any other currency is rejected at checkout.
  Auto-approve limits and daily caps are per seller and per currency (defaults GBP 50 / USD 50
  auto-approve, GBP 500 / USD 500 daily cap); amounts in different currencies are never compared
  or summed.
- PO-11 Seller liable with a reship fix → no `money_action`; the case settles with the reship
  recorded.
- PO-12 A failed refund: 5xx and timeouts are first retried with the same key up to
  `max_attempts` (PP-07); only then is the `money_action` marked `failed` and the case moved to
  HOLD_FOR_REVIEW. A 4xx marks it `failed` and moves the case to HOLD_FOR_REVIEW at once. A
  refund is never retried with a new key.
- PO-13 Checks run in this order: open dispute → hold conditions (contradiction, low confidence,
  repeat claimant) → no matching rule set → liability → daily cap → auto-approve limit →
  pre-refund re-check.

## EX — Explainer and drafts
- EX-01 Every explanation names the rule id it used.
- EX-02 Eval: explanations never accuse the buyer (DeepEval G-Eval rubric).
- EX-03 An explanation that contradicts the rule outcome is blocked; template text is used.
- EX-04 A buyer message and, for carrier faults, a claim letter are drafted.

## RG — Policy reader (RAG)
- RG-01 Answers cite the retrieved passages.
- RG-02 No relevant passage → the rule file text is quoted instead.
- RG-03 Eval: Ragas faithfulness ≥ 0.90 (config) on 30 questions.

## RK — Risk model
- RK-01 Probabilities are calibrated; calibration plot and PR-AUC saved in the eval report.
- RK-02 Every score returns its top SHAP reasons in plain words.
- RK-03 The model card states it is trained on synthetic data.

## AS — Control Room (AG Studio + AG Grid)
- AS-01 AG Studio loads Doorstep data as sources (orders, shipments, cases, money_actions) with
  relationships between them.
- AS-02 Expressions compute prevention rate and carrier recovery rate; a calendar enables
  month and quarter filters.
- AS-03 A Doorstep theme is applied (not the default look).
- AS-04 At least two custom widgets exist (evidence-completeness meter, failure-point breakdown).
- AS-05 Pre-built reports load from state: Overview, Carriers, Cases.
- AS-06 A custom Doorstep agent works with Studio's built-in agents: a question such as
  "which carrier cost me most this month?" produces a correct widget from real data.
- AS-07 The AG Grid case queue shows every open case; Approve calls the policy service and
  shows the PayPal result; addresses are masked by default.

## AT — Bounded-autonomy agent (PayPal Agent Toolkit)
- AT-01 The agent's PayPal tools come from the Agent Toolkit with an explicit allowlist
  (orders get, shipment create/show, disputes list/get) and nothing else.
- AT-02 The only money tool is `request_refund`; it calls the policy service, never PayPal directly.
- AT-03 Under the seller's auto-approve limit the agent completes a real sandbox refund end to end.
- AT-04 Above the limit the agent stops and escalates to the seller; no refund is created.
- AT-05 Every agent tool call is traced and appears in the case timeline.

## SB — Sandbox proof
- SB-01 The demo refund is visible in both the merchant and the buyer sandbox accounts.
- SB-02 The matching webhook appears as delivered in the sandbox event logs.
- SB-03 `/paypal:doctor` reports no problems on the final commit.

## BR — Bryntum deadline radar
- BR-01 One row per open shipment or case; journey bars and deadline milestones.
- BR-02 Deadlines come only from rule files or PayPal dispute data.
- BR-03 Clicking an event opens that order in the Control Room.
- BR-04 Dragging a redelivery slot calls the API and writes an audit record.

## MC — MCP tool
- MC-01 `report_delivery_problem` needs a signed token scoped to one order.
- MC-02 It cannot read or act on any other order.
- MC-03 It opens a case; it can never trigger a refund directly.

## SE — Security and safety
- SE-01 gitleaks finds no secrets; logs contain no secrets.
- SE-02 Personal fields are redacted before model calls and in traces.
- SE-03 Promptfoo red-team: 100% of money-related attacks blocked.
- SE-04 Schemathesis run finds no 500 errors and no schema violations.

## PR — Proof layer and judge path (patterns from winning projects)
- PR-01 Every money decision produces an Ed25519-signed receipt containing the previous
  receipt's hash (a hash chain).
- PR-02 Public `GET /cases/{id}/verify` recomputes the chain and reports the first broken
  link; it exposes no personal data.
- PR-03 Tamper test: editing any stored receipt makes PR-02 fail at that receipt.
- PR-04 Immediately before a refund, capture state and evidence fingerprint are re-checked;
  any change since the decision blocks the refund for re-review.
- PR-05 `GET /healthz` lists every simulated component (lockers, rerouting, claim filing).
- PR-06 `POST /demo/seed?scratch=1` gives each judge an isolated scenario that never touches
  the filmed demo data; no login needed.
- PR-07 Demo reset works only in development with an explicit confirmation phrase.
- PR-08 Status words never overclaim: a check that could not run reports `not_run` or
  `partial`, never `passed`.

## E2E — Whole product
- E2E-01 Playwright: checkout → ship → "Not here" → approve → PayPal refund id shown.
- E2E-02 `make demo` seeds the Sam and Maria scenario identically every time.
- E2E-03 A live sandbox smoke test runs the full loop against real PayPal and Shippo.
