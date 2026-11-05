"""SL-07: a seal scan from the seller's own browser never counts as possession proof (ADR-042).

The critique (Nov 5) found the seller can open any shipped order's seal page from the Control
Room. Scanning it after the delivered scan would have created the strongest possession proof
against an honest buyer. The seller's browser now carries a signed cookie; a scan that brings a
valid cookie for the order's own seller is recorded but not counted.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import AppDeps, Services
from app.cases.engine import AddressCheck, ClaimReason, FailurePoint, assess
from app.cases.signals import EvidenceKind, assemble_signals
from app.config import Settings
from app.main import create_app
from app.orders.enums import OrderState, ProofKind
from app.orders.models import AuditLog, Evidence, Order, TrackingEvent
from app.proof.receipts import ReceiptSigner
from app.proof.seal import (
    SELLER_BROWSER,
    SELLER_BROWSER_SCOPE,
    SELLER_COOKIE,
    counted_scan,
    issue_seal,
    record_scan,
)
from app.runtime import ROOT
from app.security.links import LinkSigner
from tests.factories import make_order, make_seller, make_shipment

SECRET = "seal-browser-secret-0123456789abc"


def _delivered(db, clock):
    seller = make_seller(db, region="GB")
    order = make_order(
        db,
        seller,
        state=OrderState.DELIVERED,
        buyer_country="GB",
        currency="GBP",
        amount_minor=4000,
    )
    order.address_check = AddressCheck.VALID.value
    order.item_description = "Brass desk lamp"
    shipment = make_shipment(db, order, tracking_number="SHIPPO_DELIVERED")
    for i, status in enumerate(("TRANSIT", "DELIVERED")):
        db.add(
            TrackingEvent(
                shipment_id=shipment.id,
                status=status,
                occurred_at=clock.now() - timedelta(hours=10 - i),
                applied=True,
            )
        )
    db.flush()
    return seller, order


@pytest.fixture
def links(clock) -> LinkSigner:
    return LinkSigner(SECRET, clock)


@pytest.fixture
def app(session_factory, settings, clock, links):
    services = Services(links=links, receipts=ReceiptSigner.generate(), orders=None, approved=None)
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=ROOT / "rules",
        services=services,
    )
    return create_app(deps)


def _scans(db, order_id: str) -> list[Evidence]:
    db.expire_all()
    return list(
        db.scalars(
            select(Evidence)
            .where(Evidence.order_id == order_id, Evidence.kind == EvidenceKind.SEAL_SCAN)
            .order_by(Evidence.created_at)
        )
    )


# ---------------------------------------------------------------- the rule in code


def test_SL_07_a_scan_from_the_seller_browser_is_recorded_not_counted(db, clock):
    _, order = _delivered(db, clock)
    scan = record_scan(db, order, clock.now(), seller_browser=True)
    assert scan.ai_result == {"counted": False, "reason": SELLER_BROWSER}
    assert counted_scan(db, order.id) is None
    signals, proofs = assemble_signals(db, order, ClaimReason.NOT_RECEIVED, clock.now(), Settings())
    assert ProofKind.BUYER_SEAL_SCAN not in proofs
    assert assess(signals).failure_point != FailurePoint.STOLEN_AFTER_DELIVERY


def test_SL_07_a_later_scan_from_another_device_still_counts(db, clock):
    _, order = _delivered(db, clock)
    seller_scan = record_scan(db, order, clock.now(), seller_browser=True)
    clock.advance(3600)
    buyer_scan = record_scan(db, order, clock.now())
    assert buyer_scan.id != seller_scan.id
    assert buyer_scan.ai_result == {"counted": True, "reason": "after_carrier_handover"}
    assert counted_scan(db, order.id) is not None
    # Repeats from the seller's browser change nothing once the buyer's scan counts.
    assert record_scan(db, order, clock.now(), seller_browser=True).id == buyer_scan.id


# ---------------------------------------------------------------- the cookie


def _cookie_header(response) -> str:
    return "; ".join(response.headers.get_list("set-cookie"))


def test_SL_07_opening_the_control_room_sets_a_signed_httponly_cookie(db, clock, links, app):
    seller, _ = _delivered(db, clock)
    db.commit()
    client = TestClient(app)
    r = client.get(f"/api/seller/{links.issue(seller.id, scope='seller')}/me")
    assert r.status_code == 200
    header = _cookie_header(r)
    assert f"{SELLER_COOKIE}=" in header
    assert "HttpOnly" in header and "samesite=lax" in header.lower() and "Path=/" in header
    value = client.cookies.get(SELLER_COOKIE)
    assert value and links.verify(value, scope=SELLER_BROWSER_SCOPE) == seller.id


def test_SL_07_opening_a_seal_card_also_sets_the_cookie(db, clock, links, app):
    seller, order = _delivered(db, clock)
    db.commit()
    client = TestClient(app)
    r = client.get(f"/api/seller/{links.issue(seller.id, scope='seller')}/orders/{order.id}/seal")
    assert r.status_code == 200
    assert client.cookies.get(SELLER_COOKIE)


def test_SL_07_a_bad_seller_link_sets_no_cookie(db, clock, links, app):
    client = TestClient(app)
    assert client.get("/api/seller/forged/me").status_code == 404
    assert client.cookies.get(SELLER_COOKIE) is None


# ---------------------------------------------------------------- scans through the API


def test_SL_07_seal_confirm_from_the_seller_browser_is_not_counted(db, clock, links, app):
    seller, order = _delivered(db, clock)
    db.commit()
    client = TestClient(app)
    client.get(f"/api/seller/{links.issue(seller.id, scope='seller')}/me")
    r = client.post(f"/api/seal/{issue_seal(links, order.id)}/confirm")
    assert r.status_code == 200, r.text
    assert r.json()["counted"] is False
    [scan] = _scans(db, order.id)
    assert scan.ai_result == {"counted": False, "reason": SELLER_BROWSER}
    actors = set(db.scalars(select(AuditLog.actor).where(AuditLog.entity_id == order.id)))
    assert SELLER_BROWSER in actors and "buyer_seal" not in actors


def test_SL_07_the_buyer_phone_without_the_cookie_still_counts(db, clock, links, app):
    seller, order = _delivered(db, clock)
    db.commit()
    TestClient(app).get(
        f"/api/seller/{links.issue(seller.id, scope='seller')}/me"
    )  # seller's laptop
    phone = TestClient(app)  # the buyer's phone: no cookie
    r = phone.post(f"/api/seal/{issue_seal(links, order.id)}/confirm")
    assert r.status_code == 200 and r.json()["counted"] is True
    assert db.get(Order, order.id) is not None


@pytest.mark.parametrize("kind", ["other_seller", "forged", "wrong_scope", "expired"])
def test_SL_07_a_cookie_that_is_not_this_sellers_changes_nothing(db, clock, links, app, kind):
    _, order = _delivered(db, clock)
    other = make_seller(db, region="GB")
    db.commit()
    value = {
        "other_seller": links.issue(other.id, scope=SELLER_BROWSER_SCOPE),
        "forged": "not.a-real-cookie",
        "wrong_scope": links.issue(order.seller_id, scope="seller"),
        "expired": links.issue(order.seller_id, scope=SELLER_BROWSER_SCOPE, ttl_s=1),
    }[kind]
    if kind == "expired":
        clock.advance(60)
    client = TestClient(app, cookies={SELLER_COOKIE: value})
    r = client.post(f"/api/seal/{issue_seal(links, order.id)}/confirm")
    assert r.status_code == 200 and r.json()["counted"] is True


def test_SL_07_something_wrong_from_the_seller_browser_is_not_counted(db, clock, links, app):
    seller, order = _delivered(db, clock)
    db.commit()
    client = TestClient(app)
    client.get(f"/api/seller/{links.issue(seller.id, scope='seller')}/me")
    r = client.post(f"/api/seal/{issue_seal(links, order.id)}/report", json={"reason": "damaged"})
    assert r.status_code == 200, r.text
    [scan] = _scans(db, order.id)
    assert scan.ai_result["counted"] is False and scan.ai_result["reason"] == SELLER_BROWSER


def test_SL_07_the_seal_view_tells_the_seller_their_scan_did_not_count(db, clock, links, app):
    seller, order = _delivered(db, clock)
    db.commit()
    client = TestClient(app)
    client.get(f"/api/seller/{links.issue(seller.id, scope='seller')}/me")
    body = client.post(f"/api/seal/{issue_seal(links, order.id)}/confirm").json()
    assert body["seller_browser"] is True and body["counted"] is False
    assert (
        TestClient(app).get(f"/api/seal/{issue_seal(links, order.id)}").json()["seller_browser"]
        is False
    )
    db.expire_all()
    assert db.get(Order, order.id).state == OrderState.CONFIRMED  # type: ignore[union-attr]
