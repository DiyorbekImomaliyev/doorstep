"""SA-01..05: the delivered-scan audit checks the carrier's "Delivered" claim against the
carrier's own data. Deterministic code; unknown inputs never count as a contradiction."""

from __future__ import annotations

import yaml

from app.orders.enums import CaseState, ProofKind
from app.policy.reasons import Reason
from app.policy.rules import load_rules
from app.policy.service import PolicyService
from tests.factories import make_case, make_order, make_seller
from tests.helpers import engine_result, facts_for

P = ProofKind
US_PROOF = frozenset({P.DELIVERED_SCAN, P.DELIVERY_PHOTO})


# ---------------------------------------------------------------- SA-04 contradicted scan, US


def test_SA_04_us_rule_does_not_count_a_contradicted_scan(rulebook):
    us = rulebook.select("US")
    assert us.contradicted_scan_counts_as_proof is False
    assert us.evaluate(US_PROOF).possession_proven is True
    contradicted = us.evaluate(US_PROOF, scan_contradicted=True)
    assert contradicted.possession_proven is False and contradicted.liable_party == "seller"
    # A stronger proof still stands when the scan is contradicted.
    assert us.evaluate(frozenset({P.SIGNATURE}), scan_contradicted=True).possession_proven


def test_SA_04_rule_that_counts_contradicted_scans_is_unchanged(rules_copy):
    path = rules_copy / "us_seller_policy_v1.yaml"
    data = yaml.safe_load(path.read_text())
    data["contradicted_scan_counts_as_proof"] = True
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    us = load_rules(rules_copy).select("US")
    assert us.evaluate(US_PROOF, scan_contradicted=True).possession_proven is True  # type: ignore[union-attr]


def _us_case(db):
    seller = make_seller(db, region="US")
    order = make_order(db, seller, amount_minor=3000, currency="USD", buyer_country="US")
    return order, make_case(db, order)


def test_SA_04_policy_refunds_when_the_us_scan_is_contradicted(db, rulebook, clock):
    order, case = _us_case(db)
    facts = facts_for(order, proofs=US_PROOF).model_copy(update={"scan_contradicted": True})
    decision = PolicyService(db, rulebook, clock).decide(case, engine_result(), facts)
    assert decision.route == CaseState.AUTO_REFUND
    assert Reason.SCAN_CONTRADICTED.value in [c.value for c in decision.codes]


def test_SA_04_same_us_case_without_contradiction_is_possession_proven(db, rulebook, clock):
    order, case = _us_case(db)
    decision = PolicyService(db, rulebook, clock).decide(
        case, engine_result(), facts_for(order, proofs=US_PROOF)
    )
    assert decision.route == CaseState.AWAITING_SELLER
    assert Reason.SCAN_CONTRADICTED.value not in [c.value for c in decision.codes]


def test_SA_04_uk_records_no_scan_code_because_a_scan_is_never_proof_there(db, rulebook, clock):
    order = make_order(db, make_seller(db), amount_minor=3000)
    case = make_case(db, order)
    facts = facts_for(order, proofs=frozenset({P.DELIVERED_SCAN})).model_copy(
        update={"scan_contradicted": True}
    )
    decision = PolicyService(db, rulebook, clock).decide(case, engine_result(), facts)
    assert decision.route == CaseState.AUTO_REFUND
    assert Reason.SCAN_CONTRADICTED.value not in [c.value for c in decision.codes]
