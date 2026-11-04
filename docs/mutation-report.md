# Mutation report: app/policy (ADR-023)

`make mutmut-gate` runs mutmut on `app/policy`, then `scripts/mutmut_gate.py` sorts each survivor:
**text-only** (allowed) or **logic** (fails the gate). See ADR-023 for the exact rule.

## Latest run (2026-11-04)

- 1417 mutants: **1272 killed, 145 survived** (was 1441 / 356 survived before the ADR-023 test
  addendum and reason codes).
- Survivors: **31 text-only** (allowed), **114 logic**. **The gate is red** until a test addendum
  (human approval) kills them.
- Of the 114:
  - **91 audit-trail content.** Audit `actor` / `action` / `entity_type` strings, the keys and
    `at` of audit rows, case transition reason strings, the `blocked` text on a blocked money
    action, and the audit row of `record_external_refund`. No test asserts what the audit
    rows hold.
  - **23 logic gaps** (below).

### Logic gaps (need tests)

| # | Where | Surviving mutant | What a test must pin |
| --- | --- | --- | --- |
| 1 | `RefundJobHandler.run` | `amount=None` on the refund call | A partial refund sends exactly its amount (None = full refund at PayPal). **Money-relevant.** |
| 2 | `RestRefundGateway.refund_capture` | `custom_id` dropped or None (2) | The gateway forwards `custom_id` to the REST call (ADR-022). |
| 3 | `_recheck` | recovery `and` → `or` | Recovery needs our custom_id, the same amount, **and** refunded == known + amount. |
| 4 | `_queue_refund` | partial kind → always REFUND | A queued partial refund has kind PARTIAL_REFUND. |
| 5 | `_existing_refund` | kind filter dropped | Only refund kinds count as "already queued" for a decision. |
| 6 | `_route_case` | `and` → `or` | Routing a case to its current state is a no-op, never an IllegalTransition. |
| 7 | `RefundJobHandler._load` | `or` → `and` on case_id / decision_id | A money action with no decision (but a case) is a MissingRecord. |
| 8 | `_fail_dangling` | `session.get(ma_id)`, `is None` flipped (2) | The dangling path with an existing money action marks it FAILED. |
| 9 | `RefundJobHandler.run` | `completed_at`, `paypal_status`, `now` set to None (3) | A completed refund stores `completed_at` and `paypal_status`. |
| 10 | `decide` | `model_dump(mode=...)` (6), `explanation` (2) | Decision `signals` are JSON-mode dumps; explanation is "" before the explainer runs. |
| 11 | `rules._norm` | `[^0-9a-z]` → `[^0-9A-Z]` | Address comparison keeps letters (e.g. "Flat 2A" vs "Flat 2B" differ). |
| 12 | `rules._load_file` | `data.get("kind")` → `data.get(None)` | A file's `kind` field decides its model. |


## Equivalent mutants (excluded with `# pragma: no mutate`)

Each line below is excluded at the source because no test can tell the mutant apart from the
original. A pragma without an entry here is a review failure.

| Where | Line | Mutants it hides | Why they are equivalent |
| --- | --- | --- | --- |
| `reasons.py` | `MESSAGES` table | any wording change | Human sentences only; tests assert `Reason` codes (ADR-023). |
| `service.py` `_audit` | `actor[:64]`, `action[:64]` | `[:65]`, `[:63]` | Length cap; every actor and action is a short constant. |
| `service.py` `_latest_decision` | `Decision.id.desc()` | drop or flip the tie-break | Decision times are unique per case (`_next_decision_time`), so the tie-break never decides. |
| `service.py` `_next_decision_time` | `timedelta(microseconds=1)` | `microseconds=2` | Any positive step keeps decision times strictly increasing. |
| `service.py` `_money_audit_payload` | `if approval` | `if not approval`, `True` | Every queued refund has an approval; defensive only. |
| `service.py` `_approval_for` | `order_by(Approval.at.desc())` | flip or drop the order | One approval per decision, so the order never decides. |
| `service.py` `approve` | `s.refresh(case, with_for_update=True)` | `with_for_update=False` | Row lock; only Postgres honours it (covered by the Postgres PO-07 test, not run by mutmut). |
| `service.py` `_load`, `_fail_dangling` | `job.payload.get("money_action_id", "")` | default `None`, `"XXXX"`, no default | Any default matches no money action, so the job fails the same way. |
| `service.py` `_fail_dangling` | `ma.case_id or ""` | `"XXXX"` | Same: the default matches no case. |
| `service.py` `on_failed`, `_fail_dangling` | `...[:500]` | `[:501]`, `[:499]` | Length cap on stored error text. |
| `fingerprint.py` | `blob.encode("utf-8")` | `"UTF-8"` | Codec names ignore case. |
| `rules.py` `_load_file` | `read_text(encoding="utf-8")` | `"UTF-8"`, `None` | Codec names ignore case; `None` is UTF-8 on the Linux CI runner and macOS. |

## Text-only survivors (allowed by the gate)

Changes inside logger calls, raised exception messages, `message()` / `note()` /
`_Verdict.block()` placeholder values, `_per_currency` / `_exactly_one` labels, and the type
argument of `typing.cast`. None changes what the code decides or stores as a code.
