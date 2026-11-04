# Doorstep — acceptance criteria (the burn-down list)

This file turns docs/SPEC.md into testable statements. Every criterion has an ID.
Every test name must contain the ID it covers (e.g. `test_PO_06_refunds_never_exceed_capture`).
`scripts/trace.py` fails CI if any ID has no test. A criterion that is not built yet gets a
test marked `xfail(reason="AC-ID: limitation ...")` — that is our list of known limits.

Numbers marked (config) are targets kept in config, not hardcoded.

## PP — PayPal client
- PP-01 OAuth token is cached and reused until shortly before expiry; one retry on 401.
- PP-02 `create_order` sends intent CAPTURE, amount, currency and shipping options
  Home / Locker / Signature with exactly one selected; returns order id and approve link.
- PP-03 `capture` stores `capture_id` on the order and moves it to PAID.
- PP-04 `add_tracking` sends carrier + tracking number; failures are retried by the job
  runner with backoff, max attempts (config).
- PP-05 Every write sends `PayPal-Request-Id`; the key is derived from case id + action, so a
  retry reuses the same key.
- PP-06 Every call is logged with request id, status and latency; secrets are never logged.

## WH — Webhooks
- WH-01 PayPal webhook with an invalid signature → 400 and nothing stored.
- WH-02 Valid webhook → stored in `webhook_inbox`, 200 returned, processing done by the worker.
- WH-03 Duplicate `event_id` → 200 and not processed again.
- WH-04 Shippo webhook with a wrong URL token → rejected, nothing stored.
- WH-05 Unknown event types are stored and ignored without errors.

## SM — Order state machine
- SM-01 Only the transitions listed in SPEC section 8 are allowed.
- SM-02 Any other transition raises and changes nothing.
- SM-03 Every transition writes an `audit_log` row.
- SM-04 Property test: no random event sequence reaches a refunded state without PAID first.
- SM-05 Only the case transitions listed in SPEC section 8 are allowed
  (OPEN → GATHERING_EVIDENCE → DECIDED → AUTO_REFUND | AWAITING_SELLER | HOLD_FOR_REVIEW |
  DISPUTE_EVIDENCE → SETTLED or CLOSED); any other case transition raises and changes nothing.
- SM-06 Property test: a case can never reach SETTLED without a recorded decision and its
  approval path (auto-approve under limit, seller approval, or reviewer approval).

## CK — Checkout (prevent)
- CK-01 When address validation flags a missing unit/flat, the buyer sees a fix prompt.
- CK-02 The buyer can keep the original address; the override is stored as a signal.
- CK-03 Home, Locker and Signature are offered; the recommended option follows the risk rule.
- CK-04 The chosen method is stored on the order and shipment.
- CK-05 The PayPal order contains the chosen shipping option.

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
- RL-01 Rule files are schema-validated at startup; an invalid file stops startup.
- RL-02 Every rule has `id`, `version` and `source`.
- RL-03 The rule set is chosen by buyer country and buyer type.
- RL-04 UK/EU consumer, no possession proof → seller liable.
- RL-05 UK/EU, locker code used or signature present → possession proven.
- RL-06 US → the seller's written policy decides.
- RL-07 PayPal Seller Protection: delivery to a redirected address is flagged.
- RL-08 Every decision stores the rule id and version used.

## FE — Failure-point engine
- FE-01 … FE-06 Each row of the SPEC section 7 table is detected from its typical signals
  (one test per row: bad address, carrier lost, damaged, stolen after safe delivery,
  wrong item, contradicting claim).
- FE-07 Every decision has a confidence label (high / medium / low) with reasons.
- FE-08 Contradicting signals or low confidence → HOLD_FOR_REVIEW.
- FE-09 Eval: failure-point accuracy ≥ 0.85 (config) on the 60-case set; hold-out reported
  separately.

## PO — Policy service (money)
- PO-01 Only `app/policy` can call refund or dispute writes (enforced by an import-linter
  contract test).
- PO-02 Seller liable and amount under the auto-approve limit → refund job created.
- PO-03 Over the limit → waits for seller approval.
- PO-04 Buyer with 3+ claims in 90 days (config) → human review, never auto-deny.
- PO-05 An open PayPal dispute → evidence path, not a separate refund.
- PO-06 Property test: total refunds never exceed the captured amount.
- PO-07 Approving the same case twice → exactly one refund.
- PO-08 Daily refund cap per seller (config) is enforced.
- PO-09 Every money action writes an audit record with signals, rule, approver, PayPal response.
- PO-10 All money is integer minor units plus an ISO 4217 currency code (no floats anywhere in
  the money path); auto-approve limits and daily caps are per seller and per currency (config;
  defaults GBP 50 / USD 50 auto-approve, GBP 500 / USD 500 daily cap); amounts in different
  currencies are never compared or summed.

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
