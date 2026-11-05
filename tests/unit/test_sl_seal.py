"""SL-01..06 and RT-01: the possession seal. The buyer's own phone scans the QR inside the
parcel; a scan after carrier handover proves physical possession (CRA 2015 s.29)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select

from app.ai.explainer import Explainer
from app.api.deps import AppDeps, Services
from app.cases.engine import AddressCheck, ClaimReason, FailurePoint, assess
from app.cases.pipeline import CasePipeline
from app.cases.signals import EvidenceKind, assemble_signals
from app.main import create_app
from app.mcp.server import MCP_SCOPE
from app.orders.enums import CaseState, OrderState, ProofKind
from app.orders.models import Case, Evidence, Order, TrackingEvent
from app.proof.receipts import ReceiptSigner
from app.proof.seal import (
    SEAL_SCOPE,
    SealError,
    counted_scan,
    issue_seal,
    record_scan,
    seal_svg,
    seal_url,
)
from app.runtime import ROOT
from app.security.links import InvalidLink, LinkSigner
from tests.factories import GB_ADDRESS, make_order, make_seller, make_shipment
from tests.fakes import FakeClock

SECRET = "seal-test-secret-0123456789abcdef"


def _shipped(
    db,
    clock,
    *,
    state: OrderState = OrderState.DELIVERED,
    events: tuple[str, ...] = ("TRANSIT", "DELIVERED"),
    country: str = "GB",
    currency: str = "GBP",
    amount_minor: int = 4000,
):
    seller = make_seller(db, region=country)
    order = make_order(
        db,
        seller,
        state=state,
        buyer_country=country,
        currency=currency,
        amount_minor=amount_minor,
    )
    order.address_check = AddressCheck.VALID.value
    order.item_description = "Black jacket, size M"
    shipment = make_shipment(db, order, tracking_number="SHIPPO_DELIVERED")
    for i, status in enumerate(events):
        db.add(
            TrackingEvent(
                shipment_id=shipment.id,
                status=status,
                occurred_at=clock.now() - timedelta(hours=10 - i),
                applied=True,
            )
        )
    db.add(
        Evidence(
            order_id=order.id,
            kind=EvidenceKind.PACKING_PHOTO,
            ai_result={"verdict": "match", "expected_weight_g": 900},
        )
    )
    db.flush()
    return seller, order, shipment


@pytest.fixture
def links(clock) -> LinkSigner:
    return LinkSigner(SECRET, clock)


@pytest.fixture
def api(session_factory, settings, clock, links) -> TestClient:
    services = Services(links=links, receipts=ReceiptSigner.generate(), orders=None, approved=None)
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=ROOT / "rules",
        services=services,
    )
    return TestClient(create_app(deps))


@pytest.fixture
def pipeline(db, rulebook, clock, settings) -> CasePipeline:
    return CasePipeline(db, rulebook, clock, settings, Explainer(), ReceiptSigner.generate())


# ---------------------------------------------------------------- SL-01 seal link and card


def test_SL_01_seal_token_is_scoped_to_one_order_and_one_scope(links):
    token = issue_seal(links, "order-1")
    assert links.verify(token, scope=SEAL_SCOPE) == "order-1"
    for other in ("buyer", MCP_SCOPE, "seller"):
        with pytest.raises(InvalidLink):
            links.verify(token, scope=other)
        with pytest.raises(InvalidLink):
            links.verify(links.issue("order-1", scope=other), scope=SEAL_SCOPE)


@given(order_id=st.text(min_size=1, max_size=40), scope=st.sampled_from(["buyer", "mcp", "seller"]))
def test_SL_01_property_no_other_token_ever_opens_a_seal(order_id, scope):
    signer = LinkSigner(SECRET, FakeClock())
    with pytest.raises(InvalidLink):
        signer.verify(signer.issue(order_id, scope=scope), scope=SEAL_SCOPE)
    assert signer.verify(issue_seal(signer, order_id), scope=SEAL_SCOPE) == order_id


def test_SL_01_seal_url_and_qr_svg():
    url = seal_url("https://doorstep.example/", "abc.def")
    assert url == "https://doorstep.example/s/abc.def"
    svg = seal_svg(url)
    assert svg.startswith("<svg") and "path" in svg


def test_SL_01_seller_prints_the_seal_card_for_own_orders_only(db, clock, links, api):
    seller, order, _ = _shipped(db, clock)
    other, _, _ = _shipped(db, clock)
    db.commit()
    card = api.get(f"/api/seller/{links.issue(seller.id, scope='seller')}/orders/{order.id}/seal")
    assert card.status_code == 200, card.text
    body = card.json()
    assert body["svg"].startswith("<svg") and "/s/" in body["url"]
    token = body["url"].rsplit("/s/", 1)[1]
    assert links.verify(token, scope=SEAL_SCOPE) == order.id
    stranger = api.get(
        f"/api/seller/{links.issue(other.id, scope='seller')}/orders/{order.id}/seal"
    )
    assert stranger.status_code == 404


# ---------------------------------------------------------------- SL-02 seal page


def test_SL_02_seal_page_shows_item_and_seller_never_the_address(db, clock, links, api):
    _, order, _ = _shipped(db, clock)
    db.commit()
    r = api.get(f"/api/seal/{issue_seal(links, order.id)}")
    assert r.status_code == 200
    body = r.json()
    assert body["item"] == "Black jacket, size M" and body["seller_name"] == "Maria"
    assert body["status"] == "ready" and body["scanned"] is False
    for value in GB_ADDRESS.values():
        if value:
            assert value not in r.text
    assert api.get(f"/api/seal/{links.issue(order.id, scope='buyer')}").status_code == 404
    assert api.get("/api/seal/not-a-token").status_code == 404


# ---------------------------------------------------------------- SL-03 "I have it"


def test_SL_03_i_have_it_records_one_scan_and_confirms(db, clock, links, api):
    _, order, _ = _shipped(db, clock)
    db.commit()
    token = issue_seal(links, order.id)
    first = api.post(f"/api/seal/{token}/confirm")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "confirmed" and first.json()["counted"] is True
    clock.advance(600)
    assert api.post(f"/api/seal/{token}/confirm").status_code == 200  # repeat: no change
    db.expire_all()
    scans = db.scalars(select(Evidence).where(Evidence.kind == EvidenceKind.SEAL_SCAN)).all()
    assert len(scans) == 1
    assert scans[0].ai_result == {"counted": True, "reason": "after_carrier_handover"}
    assert scans[0].uri == ""
    assert db.get(Order, order.id).state == OrderState.CONFIRMED  # type: ignore[union-attr]


def test_SL_03_scan_on_an_open_case_is_kept_as_evidence(db, clock, links, api, pipeline):
    _, order, _ = _shipped(db, clock)
    pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    db.commit()
    r = api.post(f"/api/seal/{issue_seal(links, order.id)}/confirm")
    assert r.status_code == 200 and r.json()["status"] == "case_open"
    db.expire_all()
    assert counted_scan(db, order.id) is not None


# ---------------------------------------------------------------- SL-04 counts only after handover


def test_SL_04_scan_before_carrier_handover_is_recorded_not_counted(db, clock):
    _, order, _ = _shipped(db, clock, state=OrderState.SHIPPED, events=("PRE_TRANSIT",))
    early = record_scan(db, order, clock.now())
    assert early.ai_result == {"counted": False, "reason": "before_carrier_handover"}
    assert counted_scan(db, order.id) is None
    _, proofs = assemble_signals(db, order, ClaimReason.NOT_RECEIVED, clock.now(), _settings())
    assert ProofKind.BUYER_SEAL_SCAN not in proofs
    # The carrier picks it up later; a scan after that counts and is a second row.
    shipment_id = db.scalars(select(TrackingEvent.shipment_id)).first()
    db.add(TrackingEvent(shipment_id=shipment_id, status="TRANSIT", occurred_at=clock.now()))
    clock.advance(3600)
    late = record_scan(db, order, clock.now())
    assert late.id != early.id and late.ai_result["counted"] is True
    assert record_scan(db, order, clock.now()).id == late.id
    assert record_scan(db, order, clock.now() + timedelta(days=1)).id == late.id


def test_SL_04_counted_scan_is_possession_proof(db, clock):
    _, order, _ = _shipped(db, clock)
    record_scan(db, order, clock.now())
    _, proofs = assemble_signals(db, order, ClaimReason.NOT_RECEIVED, clock.now(), _settings())
    assert ProofKind.BUYER_SEAL_SCAN in proofs


def test_SL_04_scan_before_shipping_is_refused(db, clock, links, api):
    _, order, _ = _shipped(db, clock, state=OrderState.PAID, events=())
    db.commit()
    with pytest.raises(SealError):
        record_scan(db, order, clock.now())
    assert api.post(f"/api/seal/{issue_seal(links, order.id)}/confirm").status_code == 409
    assert api.get(f"/api/seal/{issue_seal(links, order.id)}").json()["status"] == "not_shipped"


def test_SL_04_a_handover_event_after_the_scan_does_not_count(db, clock):
    _, order, shipment = _shipped(db, clock, state=OrderState.SHIPPED, events=())
    db.add(
        TrackingEvent(
            shipment_id=shipment.id, status="TRANSIT", occurred_at=clock.now() + timedelta(hours=1)
        )
    )
    assert record_scan(db, order, clock.now()).ai_result["counted"] is False


# ---------------------------------------------------------------- SL-05 rules and routing


@pytest.mark.parametrize("country", ["GB", "FR", "US"])
def test_SL_05_rule_files_accept_the_seal_as_possession_proof(rulebook, country):
    rule = rulebook.select(country)
    assert ProofKind.BUYER_SEAL_SCAN in rule.possession_proof
    assert rule.evaluate(frozenset({ProofKind.BUYER_SEAL_SCAN})).possession_proven is True


def test_SL_05_not_here_after_a_counted_scan_goes_to_the_seller(db, clock, pipeline):
    _, order, _ = _shipped(db, clock)
    record_scan(db, order, clock.now())
    pipeline.confirm_received(order)
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    assert report.failure_point == FailurePoint.STOLEN_AFTER_DELIVERY
    assert report.route == CaseState.AWAITING_SELLER  # FE-11: never an automatic denial


def test_SL_05_engine_treats_the_seal_as_possession(db, clock):
    _, order, _ = _shipped(db, clock)
    record_scan(db, order, clock.now())
    signals, _ = assemble_signals(db, order, ClaimReason.NOT_RECEIVED, clock.now(), _settings())
    assert assess(signals).failure_point == FailurePoint.STOLEN_AFTER_DELIVERY


# ---------------------------------------------------------------- SL-06 "Something's wrong"


def test_SL_06_something_wrong_records_the_scan_and_opens_a_case(db, clock, links, api):
    _, order, _ = _shipped(db, clock)
    db.commit()
    r = api.post(f"/api/seal/{issue_seal(links, order.id)}/report", json={"reason": "damaged"})
    assert r.status_code == 200, r.text
    db.expire_all()
    case = db.scalars(select(Case).where(Case.order_id == order.id)).one()
    assert case.reason == ClaimReason.DAMAGED.value and case.reported_by == "buyer"
    assert counted_scan(db, order.id) is not None
    again = api.post(f"/api/seal/{issue_seal(links, order.id)}/report", json={"reason": "damaged"})
    assert again.status_code == 409  # one open case per order


def test_SL_06_only_problem_reasons_are_accepted(db, clock, links, api):
    _, order, _ = _shipped(db, clock)
    db.commit()
    token = issue_seal(links, order.id)
    bad = api.post(f"/api/seal/{token}/report", json={"reason": "not_received"})
    assert bad.status_code == 422


# ---------------------------------------------------------------- RT-01 rights card


def test_RT_01_buyer_sees_rights_text_after_not_here(db, clock, links, api, rulebook):
    _, order, _ = _shipped(db, clock)
    db.commit()
    buyer = links.issue(order.id, scope="buyer")
    assert api.get(f"/api/buyer/{buyer}").json()["rights"] == ""
    r = api.post(f"/api/buyer/{buyer}/not-here", json={"reason": "not_received"})
    assert r.status_code == 200, r.text
    rights = rulebook.select("GB").buyer_rights
    assert "section 29" in rights and r.json()["rights"] == rights
    assert api.get(f"/api/buyer/{buyer}").json()["rights"] == rights


def _settings():
    from app.config import Settings

    return Settings()
