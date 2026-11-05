"""SL-08: a confirmation from the seller's own browser never looks like the buyer's.

Found on Nov 5 from a screenshot: after the seller tapped "I have it" on the seal page in their own
browser, the scan did not count (SL-07) but the evidence meter still showed 100%, because the
confirmation row filled "possession proof". The seller can also open the buyer's tracking link from
the Control Room and tap "Got it".
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import AppDeps, Services
from app.api.views import evidence_completeness
from app.cases.engine import AddressCheck
from app.main import create_app
from app.orders.enums import OrderState
from app.orders.models import AuditLog, Order, TrackingEvent
from app.proof.receipts import ReceiptSigner
from app.proof.seal import SELLER_BROWSER, issue_seal
from app.runtime import ROOT
from app.security.links import LinkSigner
from tests.factories import make_order, make_seller, make_shipment

SECRET = "seal-browser-secret-0123456789abc"


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


def _delivered(db, clock):
    seller = make_seller(db, region="GB")
    order = make_order(db, seller, state=OrderState.DELIVERED, buyer_country="GB", currency="GBP")
    order.address_check = AddressCheck.VALID.value
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
    db.commit()
    return seller, order


def _seller_browser(app, links, seller) -> TestClient:
    client = TestClient(app)
    assert client.get(f"/api/seller/{links.issue(seller.id, scope='seller')}/me").status_code == 200
    return client


def _actors(db, order_id: str) -> set[str]:
    db.expire_all()
    return set(db.scalars(select(AuditLog.actor).where(AuditLog.entity_id == order_id)))


def test_SL_08_got_it_from_the_seller_browser_names_the_seller_browser(db, clock, links, app):
    seller, order = _delivered(db, clock)
    client = _seller_browser(app, links, seller)
    r = client.post(f"/api/buyer/{links.issue(order.id, scope='buyer')}/got-it")
    assert r.status_code == 200, r.text
    actors = _actors(db, order.id)
    assert SELLER_BROWSER in actors and "buyer" not in actors


def test_SL_08_got_it_from_the_buyer_is_still_the_buyer(db, clock, links, app):
    seller, order = _delivered(db, clock)
    _seller_browser(app, links, seller)  # the seller's laptop, elsewhere
    r = TestClient(app).post(f"/api/buyer/{links.issue(order.id, scope='buyer')}/got-it")
    assert r.status_code == 200
    assert "buyer" in _actors(db, order.id)
    assert "possession proof" in evidence_completeness(db, db.get(Order, order.id)).present


@pytest.mark.parametrize("path", ["got-it", "seal"])
def test_SL_08_a_seller_browser_confirmation_never_fills_possession_proof(
    db, clock, links, app, path
):
    seller, order = _delivered(db, clock)
    client = _seller_browser(app, links, seller)
    if path == "got-it":
        url = f"/api/buyer/{links.issue(order.id, scope='buyer')}/got-it"
    else:
        url = f"/api/seal/{issue_seal(links, order.id)}/confirm"
    assert client.post(url).status_code == 200
    db.expire_all()
    score = evidence_completeness(db, db.get(Order, order.id))
    assert "possession proof" in score.missing
