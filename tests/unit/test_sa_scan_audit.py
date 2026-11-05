"""SA-01..05: the delivered-scan audit checks the carrier's "Delivered" claim against the
carrier's own data. Deterministic code; unknown inputs never count as a contradiction."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from app.ai.explainer import Explainer
from app.api.views import postcode_district as views_postcode_district
from app.cases.engine import AddressCheck, ClaimReason, FailurePoint
from app.cases.pipeline import CasePipeline
from app.cases.scan_audit import (
    AuditCheck,
    AuditInput,
    CarrierScan,
    CheckId,
    CheckResult,
    ScanAudit,
    SellerDelivery,
    Verdict,
    audit,
    audit_order,
)
from app.cases.signals import EvidenceKind
from app.geo import delivery_area
from app.orders.enums import CaseState, OrderState, ProofKind
from app.orders.models import Decision, Evidence, TrackingEvent
from app.policy.reasons import Reason
from app.policy.rules import load_rules
from app.policy.service import PolicyService
from app.proof.receipts import ReceiptSigner
from app.proof.seal import record_scan
from app.runtime import ROOT
from tests.factories import GB_ADDRESS, make_case, make_order, make_seller, make_shipment
from tests.helpers import engine_result, facts_for
from tuning import engine_config

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


# ---------------------------------------------------------------- shared helpers (SA-01..05)

T0 = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
NAMED = "royal_mail"  # a named carrier: its locations and times are real data


def _input(
    *,
    carrier: str = NAMED,
    destination: str | None = "N1",
    events: tuple[tuple[str, int, str | None], ...] = (
        ("TRANSIT", -120, None),
        ("DELIVERED", 0, "N1"),
    ),
    seller: tuple[tuple[int, str | None], ...] = (),
    possession: bool = False,
    reported_after_min: int = 60,
) -> AuditInput:
    """events: (status, minutes from T0, area); seller: (seconds from T0, destination area)."""
    return AuditInput(
        carrier=carrier,
        destination_area=destination,
        events=tuple(
            CarrierScan(status=s, occurred_at=T0 + timedelta(minutes=m), area=a)
            for s, m, a in events
        ),
        seller_deliveries=tuple(
            SellerDelivery(occurred_at=T0 + timedelta(seconds=sec), area=a) for sec, a in seller
        ),
        possession_proven=possession,
        reported_at=T0 + timedelta(minutes=reported_after_min),
    )


def _check(result: ScanAudit, check: CheckId) -> AuditCheck:
    [found] = [c for c in result.checks if c.id == check]
    return found


def _event(db, shipment, status, at, *, zip_code=None, country=None) -> TrackingEvent:
    location = {k: v for k, v in {"zip": zip_code, "country": country}.items() if v}
    raw = {"tracking_status": {"status": status, **({"location": location} if location else {})}}
    event = TrackingEvent(
        shipment_id=shipment.id, status=status, occurred_at=at, applied=True, raw=raw
    )
    db.add(event)
    db.flush()
    return event


def _delivered_order(
    db,
    clock,
    seller=None,
    *,
    carrier: str = NAMED,
    postcode: str = "N1 9GU",
    country: str = "GB",
    scan_zip: str | None = None,
    scan_at=None,
):
    seller = seller or make_seller(db, region=country)
    currency = "GBP" if country == "GB" else "USD"
    order = make_order(db, seller, amount_minor=3000, currency=currency, buyer_country=country)
    order.shipping_address = {**GB_ADDRESS, "postal_code": postcode, "country": country}
    order.address_check = AddressCheck.VALID.value
    shipment = make_shipment(db, order, carrier=carrier, tracking_number=f"T-{order.id[:8]}")
    shipment.delivery_address = dict(order.shipping_address)
    delivered_at = scan_at or clock.now() - timedelta(hours=2)
    _event(db, shipment, "TRANSIT", delivered_at - timedelta(hours=8))
    _event(db, shipment, "DELIVERED", delivered_at, zip_code=scan_zip, country=country)
    return seller, order, shipment


@pytest.fixture
def pipeline(db, rulebook, clock, settings) -> CasePipeline:
    return CasePipeline(db, rulebook, clock, settings, Explainer(), ReceiptSigner.generate())


def _stored_audit(db, report) -> dict:
    decision = db.get(Decision, report.decision_id)
    return decision.signals["scan_audit"]


# ---------------------------------------------------------------- SA-01 verdicts, stored


@pytest.mark.parametrize(
    ("kwargs", "verdict"),
    [
        ({"events": (("TRANSIT", -60, None),)}, Verdict.NO_SCAN),
        ({"events": ()}, Verdict.NO_SCAN),
        ({}, Verdict.UNVERIFIED),
        ({"possession": True}, Verdict.CONSISTENT),
        ({"events": (("TRANSIT", -60, None), ("DELIVERED", 0, "E14"))}, Verdict.CONTRADICTED),
        # Possession proven by the seal does not hide a contradiction in the carrier's own data.
        (
            {"events": (("TRANSIT", -60, None), ("DELIVERED", 0, "E14")), "possession": True},
            Verdict.CONTRADICTED,
        ),
    ],
)
def test_SA_01_verdicts(kwargs, verdict):
    result = audit(_input(**kwargs))
    assert result.verdict == verdict
    assert {c.id for c in result.checks} == set(CheckId)  # every check, every time
    assert all(c.finding for c in result.checks)


def test_SA_01_every_case_stores_its_scan_audit_on_the_decision(db, clock, pipeline):
    _, order, _ = _delivered_order(db, clock, carrier="shippo")
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    stored = _stored_audit(db, report)
    assert stored["verdict"] == Verdict.UNVERIFIED
    assert {c["id"] for c in stored["checks"]} == {c.value for c in CheckId}


def test_SA_01_counted_seal_scan_makes_the_scan_consistent(db, clock, pipeline):
    _, order, _ = _delivered_order(db, clock, scan_zip="N1 7AA")
    record_scan(db, order, clock.now())
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    assert _stored_audit(db, report)["verdict"] == Verdict.CONSISTENT
    assert report.route == CaseState.AWAITING_SELLER  # never an automatic denial (FE-11)


def test_SA_01_order_without_a_shipment_has_no_scan(db, clock):
    order = make_order(db, make_seller(db), state=OrderState.IN_TRANSIT)
    assert audit_order(db, order, frozenset(), clock.now()).verdict == Verdict.NO_SCAN


# ---------------------------------------------------------------- SA-02 location


@pytest.mark.parametrize(
    ("postcode", "country", "area"),
    [
        ("N1 9GU", "GB", "N1"),
        ("n19gu", "GB", "N1"),
        ("EC1A 1BB", "GB", "EC1A"),
        ("SW1A1AA", "uk", "SW1A"),
        ("E14", "GB", "E14"),
        ("94107", "US", "94107"),
        ("94107-1234", "US", "94107"),
        ("941071234", "US", "94107"),
        ("", "GB", None),
        ("not a postcode", "GB", None),
        ("9410", "US", None),
        ("N1 9GU", "US", None),
        ("75001", "FR", None),
    ],
)
def test_SA_02_delivery_area_is_the_district_or_zip5_or_unknown(postcode, country, area):
    assert delivery_area(postcode, country) == area


def test_SA_02_display_district_still_available_from_views():
    assert views_postcode_district("N1 9GU") == "N1"
    assert views_postcode_district("94107") == "941xx"
    assert views_postcode_district("  ") == ""


@pytest.mark.parametrize(
    ("destination", "scan_area", "carrier", "result"),
    [
        ("N1", "N1", NAMED, CheckResult.SUPPORTS),
        ("N1", "E14", NAMED, CheckResult.CONTRADICTS),
        ("94107", "94110", "usps", CheckResult.CONTRADICTS),  # same 941xx district, other ZIP5
        ("N1", None, NAMED, CheckResult.UNKNOWN),  # the scan has no location
        (None, "E14", NAMED, CheckResult.UNKNOWN),  # the order's postcode did not parse
        ("N1", "E14", "shippo", CheckResult.UNKNOWN),  # Shippo test carrier: sample locations
    ],
)
def test_SA_02_location_check(destination, scan_area, carrier, result):
    audited = audit(
        _input(
            carrier=carrier,
            destination=destination,
            events=(("TRANSIT", -60, None), ("DELIVERED", 0, scan_area)),
        )
    )
    check = _check(audited, CheckId.LOCATION)
    assert check.result == result
    if result == CheckResult.CONTRADICTS:
        assert scan_area in check.finding and destination in check.finding  # type: ignore[operator]
        assert audited.verdict == Verdict.CONTRADICTED


def test_SA_02_location_is_read_from_the_stored_carrier_event(db, clock, pipeline):
    _, order, _ = _delivered_order(db, clock, scan_zip="E14 5AB")
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    stored = _stored_audit(db, report)
    assert stored["verdict"] == Verdict.CONTRADICTED
    [location] = [c for c in stored["checks"] if c["id"] == CheckId.LOCATION]
    assert "E14" in location["finding"] and "N1" in location["finding"]
    assert "High Street" not in str(stored)  # areas only, never the address (hard rule 9)


def test_SA_02_shippo_test_carrier_location_never_contradicts(db, clock):
    _, order, _ = _delivered_order(db, clock, carrier="shippo", scan_zip="E14 5AB")
    result = audit_order(db, order, frozenset(), clock.now())
    assert _check(result, CheckId.LOCATION).result == CheckResult.UNKNOWN
    assert result.verdict == Verdict.UNVERIFIED


# ---------------------------------------------------------------- SA-03 sequence, batch, soon


@pytest.mark.parametrize(
    ("events", "reported_after_min", "result"),
    [
        ((("TRANSIT", -60, None), ("DELIVERED", 0, "N1")), 60, CheckResult.SUPPORTS),
        ((("DELIVERED", 0, "N1"), ("TRANSIT", 30, None)), 60, CheckResult.CONTRADICTS),
        ((("PRE_TRANSIT", -60, None), ("DELIVERED", 0, "N1")), 60, CheckResult.UNKNOWN),
        # Dated well after the buyer's report: a scan from the future.
        ((("TRANSIT", -60, None), ("DELIVERED", 0, "N1")), -120, CheckResult.CONTRADICTS),
    ],
)
def test_SA_03_sequence_check(events, reported_after_min, result):
    audited = audit(_input(events=events, reported_after_min=reported_after_min))
    assert _check(audited, CheckId.SEQUENCE).result == result


def test_SA_03_small_clock_skew_is_not_a_future_scan():
    audited = audit(_input(reported_after_min=-5))  # carrier clock 5 minutes ahead of ours
    assert _check(audited, CheckId.SEQUENCE).result == CheckResult.SUPPORTS


@pytest.mark.parametrize(
    ("seller", "result"),
    [
        (((10, "E14"), (-20, "SE1")), CheckResult.CONTRADICTS),  # 3 districts within 60 s
        (((10, "E14"), (200, "SE1")), CheckResult.SUPPORTS),  # one is outside the window
        (((10, "N1"), (-20, "N1")), CheckResult.SUPPORTS),  # same district: one round
        (((10, "E14"), (-20, None)), CheckResult.SUPPORTS),  # unknown areas never count
        ((), CheckResult.SUPPORTS),
    ],
)
def test_SA_03_batch_scan_check(seller, result):
    check = _check(audit(_input(seller=seller)), CheckId.BATCH)
    assert check.result == result
    if result == CheckResult.CONTRADICTS:
        assert "3" in check.finding


def test_SA_03_batch_thresholds_come_from_the_tuning_config(monkeypatch):
    monkeypatch.setattr(engine_config, "SCAN_AUDIT_BATCH_MIN", 2)
    check = _check(audit(_input(seller=((10, "E14"),))), CheckId.BATCH)
    assert check.result == CheckResult.CONTRADICTS
    monkeypatch.setattr(engine_config, "SCAN_AUDIT_BATCH_WINDOW_S", 5)
    assert _check(audit(_input(seller=((10, "E14"),))), CheckId.BATCH).result == (
        CheckResult.SUPPORTS
    )


def test_SA_03_batch_on_the_shippo_test_carrier_is_unknown():
    check = _check(
        audit(_input(carrier="shippo", seller=((10, "E14"), (-20, "SE1")))), CheckId.BATCH
    )
    assert check.result == CheckResult.UNKNOWN


def test_SA_03_batch_scan_found_across_the_sellers_stored_parcels(db, clock, pipeline):
    at = clock.now() - timedelta(hours=2)
    seller, order, _ = _delivered_order(db, clock, scan_at=at)
    _delivered_order(db, clock, seller, postcode="E14 5AB", scan_at=at + timedelta(seconds=20))
    _delivered_order(db, clock, seller, postcode="SE1 7PB", scan_at=at - timedelta(seconds=30))
    # Ignored: another carrier, another seller, and a parcel outside the window.
    _delivered_order(db, clock, seller, carrier="evri", postcode="W1A 1AA", scan_at=at)
    _delivered_order(db, clock, postcode="W2 1AA", scan_at=at)
    _delivered_order(db, clock, seller, postcode="W3 1AA", scan_at=at + timedelta(minutes=5))
    result = audit_order(db, order, frozenset(), clock.now())
    batch = _check(result, CheckId.BATCH)
    assert batch.result == CheckResult.CONTRADICTS and "3" in batch.finding
    assert result.verdict == Verdict.CONTRADICTED


def test_SA_03_two_parcels_are_not_a_batch(db, clock):
    at = clock.now() - timedelta(hours=2)
    seller, order, _ = _delivered_order(db, clock, scan_at=at)
    _delivered_order(db, clock, seller, postcode="E14 5AB", scan_at=at + timedelta(seconds=20))
    assert _check(audit_order(db, order, frozenset(), clock.now()), CheckId.BATCH).result == (
        CheckResult.SUPPORTS
    )


@pytest.mark.parametrize("reported_after_min", [30, 60 * 24 * 3, -120])
def test_SA_03_reported_soon_is_context_only(reported_after_min):
    audited = audit(_input(reported_after_min=reported_after_min, events=(("DELIVERED", 0, "N1"),)))
    check = _check(audited, CheckId.REPORTED_SOON)
    assert check.result == CheckResult.CONTEXT
    assert (
        audited.verdict != Verdict.CONTRADICTED
        or _check(audited, CheckId.SEQUENCE).result == CheckResult.CONTRADICTS
    )


def test_SA_03_reported_soon_finding_names_the_window():
    within = _check(audit(_input(reported_after_min=90)), CheckId.REPORTED_SOON)
    later = _check(audit(_input(reported_after_min=60 * 30)), CheckId.REPORTED_SOON)
    assert "within 24 h" in within.finding and "within" not in later.finding


# ---------------------------------------------------------------- SA-04 through the pipeline


def test_SA_04_pipeline_passes_a_contradicted_us_scan_to_the_policy(db, clock, pipeline):
    _, order, _ = _delivered_order(
        db, clock, carrier="usps", postcode="94107", country="US", scan_zip="94110"
    )
    db.add(Evidence(order_id=order.id, kind=EvidenceKind.DOORSTEP_PHOTO, ai_result={}))
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    decision = db.get(Decision, report.decision_id)
    assert decision.signals["facts"]["scan_contradicted"] is True
    assert Reason.SCAN_CONTRADICTED.value in decision.reason_codes
    assert decision.signals["liability"]["possession_proven"] is False
    assert decision.signals["liability"]["liable_party"] == "seller"
    # The carrier's photo says "at the door", its own location says elsewhere: the engine names
    # both failure points and a person decides (FE-11, hard rule 7), never an automatic outcome.
    assert set(decision.signals["engine"]["candidates"]) == {
        "STOLEN_AFTER_DELIVERY",
        "CARRIER_LOST",
    }
    assert report.route == CaseState.AWAITING_SELLER


def test_SA_04_contradicted_scan_points_the_engine_at_carrier_lost(db, clock, pipeline):
    _, order, _ = _delivered_order(db, clock, scan_zip="E14 5AB")
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    decision = db.get(Decision, report.decision_id)
    assert report.failure_point == FailurePoint.CARRIER_LOST
    assert "the carrier's own data contradicts its delivered scan" in decision.reasons
    assert decision.signals["facts"]["scan_contradicted"] is True
    assert report.route == CaseState.AUTO_REFUND  # UK: no possession proof, seller's risk


def test_SA_04_same_us_case_with_a_consistent_scan_is_possession_proven(db, clock, pipeline):
    _, order, _ = _delivered_order(
        db, clock, carrier="usps", postcode="94107", country="US", scan_zip="94107"
    )
    db.add(Evidence(order_id=order.id, kind=EvidenceKind.DOORSTEP_PHOTO, ai_result={}))
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    decision = db.get(Decision, report.decision_id)
    assert decision.signals["facts"]["scan_contradicted"] is False
    assert decision.signals["liability"]["possession_proven"] is True
    assert report.route == CaseState.AWAITING_SELLER  # buyer's risk: the seller decides


# ---------------------------------------------------------------- SA-05 deterministic code


def test_SA_05_audit_module_never_imports_a_model():
    tree = ast.parse((ROOT / "app" / "cases" / "scan_audit.py").read_text())
    modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(m.startswith(("app.ai", "anthropic", "google", "langchain")) for m in modules)


_when = st.integers(min_value=-600, max_value=600)
_area = st.one_of(st.none(), st.sampled_from(["N1", "E14", "SE1", "W2", "94107", "94110"]))
_inputs = st.builds(
    lambda carrier, dest, events, seller, possession, after: _input(
        carrier=carrier,
        destination=dest,
        events=tuple(events),
        seller=tuple(seller),
        possession=possession,
        reported_after_min=after,
    ),
    st.sampled_from([NAMED, "usps", "shippo"]),
    _area,
    st.lists(
        st.tuples(
            st.sampled_from(["PRE_TRANSIT", "TRANSIT", "DELIVERED", "FAILURE"]), _when, _area
        ),
        max_size=6,
    ),
    st.lists(st.tuples(st.integers(-300, 300), _area), max_size=6),
    st.booleans(),
    st.integers(min_value=-600, max_value=6000),
)


@given(inp=_inputs)
def test_SA_05_property_same_inputs_same_audit(inp):
    assert audit(inp) == audit(inp.model_copy(deep=True))


@given(inp=_inputs)
def test_SA_05_property_unknown_locations_never_contradict(inp):
    stripped = inp.model_copy(
        update={
            "destination_area": None,
            "events": tuple(e.model_copy(update={"area": None}) for e in inp.events),
            "seller_deliveries": tuple(
                d.model_copy(update={"area": None}) for d in inp.seller_deliveries
            ),
        }
    )
    result = audit(stripped)
    assert _check(result, CheckId.LOCATION).result != CheckResult.CONTRADICTS
    assert _check(result, CheckId.BATCH).result != CheckResult.CONTRADICTS
    if _check(result, CheckId.SEQUENCE).result != CheckResult.CONTRADICTS:
        assert result.verdict != Verdict.CONTRADICTED


@given(inp=_inputs)
def test_SA_05_property_test_carrier_data_only_contradicts_by_sequence(inp):
    result = audit(inp.model_copy(update={"carrier": "shippo"}))
    assert _check(result, CheckId.LOCATION).result == (CheckResult.UNKNOWN)
    assert _check(result, CheckId.BATCH).result in (CheckResult.UNKNOWN,)
