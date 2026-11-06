# Mutation report: app/policy (ADR-023)

`make mutmut-gate` runs mutmut on `app/policy`, then `scripts/mutmut_gate.py` sorts each survivor:
**text-only** (allowed) or **logic** (fails the gate). See ADR-023 for the exact rule.

## Latest run (2026-11-07, clean `make mutmut-gate`, then a reset rerun of `autopilot.py`)

- 2402 mutants: **2341 killed, 61 survived**. Gate: **OK, zero logic survivors.**
- The 61 survivors are all text-only (allowed by the gate).
- The clean run found 4 logic survivors in `autopilot.py`. None was a money bug: two were
  equivalent (an inferred join that gives the same rows) and two only show on Postgres (a sort
  SQLite's unique index already gives). Each now has a pragma and a row below; the Postgres one
  has its own test, which fails without the sort.
- Earlier run (2026-11-04): 1386 mutants, 1352 killed, 2 timeout, 32 text-only survivors. The
  2 timeouts were detected failures, not survivors: `_order_money` without `amount_minor`
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
  here: 94 "survivors" with a stale cache, 8 on a clean run.) Editing a mutated file is a
  second reason: mutmut renumbers that file's mutants but keeps the old verdicts under the same
  names (seen on 2026-11-07: four "survivors" whose diffs were mutants the tests kill). To
  recheck one file, delete its `.meta` under `mutants/` first.
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
| `carriers.py` `load_carrier_terms` | `read_text(encoding="utf-8")` | `"UTF-8"`, `None` | Same as `rules.py`. |
| `carriers.py` `normalise` | `collapsed.strip("_")` | `strip("XX_XX")`, `strip()` variants | After the `[^a-z0-9]+` collapse only `_` can lead or trail. |
| `service.py` `_refunded_today` | `select(Seller.id)...with_for_update()` | drop the lock | Row lock that serialises the daily cap (R25); only Postgres honours it (`tests/integration/test_pg_r25_r27.py`, which fails without it; not run by mutmut). |
| `evidence.py` `_load`, `on_failed` | `job.payload.get("money_action_id", "")` | default `None`, `"XXXX"`, no default | Any default matches no money action (same as `service.py` `_load`). |
| `evidence.py` `run` | `job.payload.get("dispute_id", "")` | default `None`, `"XXXX"`, no default | Any default matches no dispute, so the job is blocked either way. |
| `evidence.py` `_open_dispute_row` | `order_by(Dispute.opened_at.desc())` | flip or drop the order | At most one open dispute per order, so the order never decides. |
| `autopilot.py` `_approvals` | `join(Decision, ...)`, `join(Case, ...)` ON clauses | drop the ON clause | Each is the only foreign key between the two tables, so SQLAlchemy infers the same join. |
| `autopilot.py` `_declined` | `AuditLog.entity_type == "case"` | drop or change the filter | Only case transitions write DECLINED, and the join needs a case id. |
| `autopilot.py` `_declined` | `failure_point.is_not(None)`, `currency.is_not(None)` | drop the filter | A kind with a missing part never matches a precedent kind, which needs both. |
| `autopilot.py` `active_rules` | `order_by(failure_point, currency)` | drop the currency sort | SQLite's unique index (seller, kind, currency) already returns this order, so the unit suite cannot see it; `tests/integration/test_pg_ap_rule_order.py` turns index scans off on Postgres and fails without the sort. |
| `autopilot.py` `statement` | `outerjoin(Recovery, Recovery.case_id == MoneyAction.case_id)` | drop the ON clause | An order has at most one case (RESOLVED is terminal), so the inferred join on the order id gives the same rows. |

## Text-only survivors (allowed by the gate)

Changes inside logger calls, raised exception messages, `message()` / `note()` /
`_Verdict.block()` placeholder values, `_per_currency` / `_exactly_one` labels, and the type
argument of `typing.cast`. None changes what the code decides or stores as a code.
