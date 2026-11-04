# Mutation report: app/policy (ADR-023)

`make mutmut-gate` runs mutmut on `app/policy`, then `scripts/mutmut_gate.py` sorts each survivor:
**text-only** (allowed) or **logic** (fails the gate). See ADR-023 for the exact rule.

## Latest run (2026-11-04, clean `make mutmut-gate`)

- 1386 mutants: **1352 killed, 2 timeout, 32 survived**. Gate: **OK, zero logic survivors.**
- The 32 survivors are all text-only (allowed by the gate).
- The 2 timeouts are detected failures, not survivors: `_order_money` without `amount_minor`
  (a ValidationError) and `_next_decision_time` with `last is None and ...` (an attribute read
  on None). Both turn into slow retry loops and hit the time limit.
- History: 356 survivors → 145 (reason codes + first addendum) → 0 logic (the gap and
  audit-golden addendum, `tests/unit/test_po_mutation_gaps.py`, plus the equivalents below).
- The audit trail is pinned by one table-driven golden test,
  `test_PO_09_audit_rows_match_golden`. It checks the full audit row (actor, action, entity
  type, entity, before, full after payload, timestamp) for eight cases: auto refund, seller
  refund, blocked, failed, record missing, external refund, decline, and reship.

### Running it

- `make mutmut-gate` deletes `mutants/` first. mutmut caches its map of which tests cover which
  function, so a cached run misses tests added since then and reports false survivors. (Seen
  here: 94 "survivors" with a stale cache, 8 on a clean run.)
- A full parallel run can also report a few false survivors. A clean rerun of all 144
  survivors on 2026-11-04 flipped 6 to killed. That only errs towards failing the gate, never
  towards passing it.

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
| `service.py` `decide` | `result.model_dump(mode="json")`, `outcome.model_dump(mode="json") if outcome` | `mode=None`, `"JSON"`, `"XXjsonXX"` (python mode) | EngineResult and LiabilityOutcome hold only StrEnums, strings, lists and bools; a StrEnum member is stored as the same JSON as its value. (`if not outcome` is pinned by `test_PO_09_decision_without_rule_records_no_liability`.) |
| `service.py` `_load` | `session.get(Case, ...) if ma.case_id`, `session.get(Decision, ...) if did` | guard always true | `session.get` with a NULL key finds nothing, same as the guard: the job fails with MissingRecord either way. |
| `fingerprint.py` | `blob.encode("utf-8")` | `"UTF-8"` | Codec names ignore case. |
| `rules.py` `_load_file` | `read_text(encoding="utf-8")` | `"UTF-8"`, `None` | Codec names ignore case; `None` is UTF-8 on the Linux CI runner and macOS. |

## Text-only survivors (allowed by the gate)

Changes inside logger calls, raised exception messages, `message()` / `note()` /
`_Verdict.block()` placeholder values, `_per_currency` / `_exactly_one` labels, and the type
argument of `typing.cast`. None changes what the code decides or stores as a code.
