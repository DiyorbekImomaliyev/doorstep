"""FE — Failure-point engine (SPEC section 7). Deterministic code; the AI only explains."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.cases.engine import (
    AddressCheck,
    ClaimReason,
    Confidence,
    FailurePoint,
    PackingCheck,
    Signals,
    assess,
)
from app.orders.enums import CaseState, Fix, ProofKind
from app.policy.service import PolicyService
from tests.factories import make_case, make_order, make_seller, money_actions
from tests.helpers import engine_result, facts_for
from tuning import engine_config

FP = FailurePoint
CONF_RANK = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}


# ---------------------------------------------------------------- FE-01 .. FE-06


@pytest.mark.parametrize(
    "signals",
    [
        Signals(claim_reason=ClaimReason.NOT_RECEIVED, address_check=AddressCheck.INVALID),
        Signals(claim_reason=ClaimReason.NOT_RECEIVED, address_check=AddressCheck.OVERRIDDEN),
        Signals(
            claim_reason=ClaimReason.NOT_RECEIVED,
            address_check=AddressCheck.VALID,
            carrier_address_issue=True,
        ),
    ],
    ids=["check-failed", "buyer-override", "carrier-address-issue"],
)
def test_FE_01_bad_address_detected(signals):
    assert assess(signals).failure_point == FP.BAD_ADDRESS


@pytest.mark.parametrize(
    "signals",
    [
        Signals(
            claim_reason=ClaimReason.NOT_RECEIVED,
            address_check=AddressCheck.VALID,
            delivered_scan=False,
            tracking_stale=True,
            packing_check=PackingCheck.MATCH,
        ),
        Signals(
            claim_reason=ClaimReason.NOT_RECEIVED,
            address_check=AddressCheck.VALID,
            delivered_scan=True,
            proofs=frozenset({ProofKind.DELIVERED_SCAN}),
            packing_check=PackingCheck.MATCH,
        ),
    ],
    ids=["stale-no-scan", "delivered-scan-no-possession-proof"],
)
def test_FE_02_carrier_lost_detected(signals):
    assert assess(signals).failure_point == FP.CARRIER_LOST


def test_FE_03_damaged_in_transit_detected():
    signals = Signals(
        claim_reason=ClaimReason.DAMAGED,
        address_check=AddressCheck.VALID,
        delivered_scan=True,
        packing_check=PackingCheck.MATCH,
        buyer_photo_damage=True,
    )
    assert assess(signals).failure_point == FP.DAMAGED_IN_TRANSIT


@pytest.mark.parametrize("proof", [ProofKind.LOCKER_PICKUP_CODE, ProofKind.SIGNATURE])
def test_FE_04_stolen_after_safe_delivery_detected(proof):
    signals = Signals(
        claim_reason=ClaimReason.NOT_RECEIVED,
        address_check=AddressCheck.VALID,
        delivered_scan=True,
        proofs=frozenset({proof}),
        packing_check=PackingCheck.MATCH,
    )
    assert assess(signals).failure_point == FP.STOLEN_AFTER_DELIVERY


def test_FE_05_wrong_item_detected():
    signals = Signals(
        claim_reason=ClaimReason.WRONG_ITEM,
        address_check=AddressCheck.VALID,
        delivered_scan=True,
        packing_check=PackingCheck.MISMATCH,
        packing_mismatch_cleared=False,
    )
    assert assess(signals).failure_point == FP.WRONG_ITEM


def test_FE_05_cleared_mismatch_is_not_wrong_item():
    signals = Signals(
        claim_reason=ClaimReason.WRONG_ITEM,
        address_check=AddressCheck.VALID,
        delivered_scan=True,
        packing_check=PackingCheck.MISMATCH,
        packing_mismatch_cleared=True,
    )
    assert FP.WRONG_ITEM not in assess(signals).candidates


@pytest.mark.parametrize("reason", [ClaimReason.EMPTY_BOX, ClaimReason.MISSING_ITEM])
def test_FE_06_contradicting_claim_detected(reason):
    signals = Signals(
        claim_reason=reason,
        address_check=AddressCheck.VALID,
        delivered_scan=True,
        packing_check=PackingCheck.MATCH,
        label_weight_matches=True,
    )
    result = assess(signals)
    assert result.failure_point == FP.CLAIM_CONTRADICTS_EVIDENCE
    assert result.contradiction is True


# ---------------------------------------------------------------- FE-07

any_signals = st.builds(
    Signals,
    claim_reason=st.sampled_from(list(ClaimReason)),
    address_check=st.sampled_from(list(AddressCheck)),
    carrier_address_issue=st.booleans(),
    delivered_scan=st.booleans(),
    tracking_stale=st.booleans(),
    proofs=st.frozensets(st.sampled_from(list(ProofKind))),
    packing_check=st.sampled_from(list(PackingCheck)),
    packing_mismatch_cleared=st.booleans(),
    buyer_photo_damage=st.none() | st.booleans(),
    label_weight_matches=st.none() | st.booleans(),
)


@given(signals=any_signals)
def test_FE_07_every_result_has_confidence_and_reasons(signals):
    result = assess(signals)
    assert result.confidence in set(Confidence)
    assert result.reasons and all(isinstance(r, str) and r for r in result.reasons)
    assert result.failure_point in result.candidates


def test_FE_07_tie_broken_by_configured_priority():
    signals = Signals(
        claim_reason=ClaimReason.DAMAGED,
        address_check=AddressCheck.VALID,
        delivered_scan=True,
        packing_check=PackingCheck.MISMATCH,
        buyer_photo_damage=True,
    )
    result = assess(signals)
    assert len(result.candidates) >= 2
    priority = list(engine_config.FAILURE_POINT_PRIORITY)
    assert result.failure_point == min(result.candidates, key=priority.index)


def test_FE_07_missing_signal_lowers_confidence_and_is_not_a_match():
    base = dict(
        claim_reason=ClaimReason.DAMAGED,
        address_check=AddressCheck.VALID,
        delivered_scan=True,
        buyer_photo_damage=True,
    )
    with_packing = assess(Signals(**base, packing_check=PackingCheck.MATCH))
    without = assess(Signals(**base, packing_check=PackingCheck.MISSING))
    assert CONF_RANK[without.confidence] < CONF_RANK[with_packing.confidence]
    assert "packing_check" in without.missing_signals
    assert "packing_check" not in with_packing.missing_signals


# ---------------------------------------------------------------- FE-08


def test_FE_08_contradiction_routes_to_hold_for_review(db, rulebook, clock):
    order = make_order(db, make_seller(db), amount_minor=3000)
    case = make_case(db, order)
    result = engine_result(FP.CLAIM_CONTRADICTS_EVIDENCE, Confidence.HIGH, contradiction=True)
    decision = PolicyService(db, rulebook, clock).decide(case, result, facts_for(order))
    assert decision.route == CaseState.HOLD_FOR_REVIEW
    assert money_actions(db, case_id=case.id) == []


def test_FE_08_low_confidence_routes_to_hold_for_review(db, rulebook, clock):
    order = make_order(db, make_seller(db), amount_minor=3000)
    case = make_case(db, order)
    decision = PolicyService(db, rulebook, clock).decide(
        case, engine_result(FP.CARRIER_LOST, Confidence.LOW), facts_for(order)
    )
    assert decision.route == CaseState.HOLD_FOR_REVIEW
    assert money_actions(db, case_id=case.id) == []


# ---------------------------------------------------------------- FE-10

SAMPLE = Signals(
    claim_reason=ClaimReason.DAMAGED,
    address_check=AddressCheck.VALID,
    delivered_scan=True,
    packing_check=PackingCheck.MISMATCH,
    buyer_photo_damage=True,
)


def test_FE_10_engine_is_deterministic():
    first = assess(SAMPLE)
    for _ in range(50):
        assert assess(SAMPLE) == first


def test_FE_10_engine_never_calls_model(monkeypatch):
    import app.ai.llm

    def boom(**_: object) -> None:
        raise AssertionError("engine called a model")

    monkeypatch.setattr(app.ai.llm, "complete_structured", boom)
    assess(SAMPLE)


def test_FE_10_engine_reads_weights_from_tuning_config(monkeypatch):
    before = assess(SAMPLE)
    assert len(before.candidates) >= 2
    monkeypatch.setattr(
        engine_config,
        "FAILURE_POINT_PRIORITY",
        tuple(reversed(engine_config.FAILURE_POINT_PRIORITY)),
    )
    after = assess(SAMPLE)
    assert after.failure_point != before.failure_point


# ---------------------------------------------------------------- FE-11


def test_FE_11_stolen_after_delivery_routes_to_seller_never_denied(db, rulebook, clock):
    order = make_order(db, make_seller(db), amount_minor=3000)
    case = make_case(db, order)
    decision = PolicyService(db, rulebook, clock).decide(
        case,
        engine_result(FP.STOLEN_AFTER_DELIVERY, Confidence.HIGH),
        facts_for(order, proofs=frozenset({ProofKind.LOCKER_PICKUP_CODE})),
    )
    assert decision.route == CaseState.AWAITING_SELLER
    assert case.state == CaseState.AWAITING_SELLER
    assert decision.reasons
    assert money_actions(db, case_id=case.id) == []


def test_FE_11_bad_address_after_buyer_override_routes_to_seller(db, rulebook, clock):
    order = make_order(db, make_seller(db), amount_minor=3000)
    order.address_override = True
    case = make_case(db, order)
    decision = PolicyService(db, rulebook, clock).decide(
        case, engine_result(FP.BAD_ADDRESS, Confidence.HIGH), facts_for(order, fix=Fix.RESHIP)
    )
    assert decision.route == CaseState.AWAITING_SELLER
    assert case.state not in (CaseState.CLOSED, CaseState.SETTLED)
