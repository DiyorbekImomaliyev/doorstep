"""R24 (review S3): the country that picks the legal rule is where the parcel goes, per PayPal.

buyer_country came from the unauthenticated checkout request, and nothing checked it against the
shipping address PayPal returned. A US buyer could post GB and get the UK possession rule (seller
liable without proof); a UK buyer could post US and lose it. The approved address now decides.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.checkout.address import AddressChecker
from app.checkout.service import CheckoutService
from app.orders.models import AuditLog
from tests.fakes_journey import SAM_FIXED, RecordingPayPal
from tests.unit.test_ck_checkout import _started


@pytest.fixture
def paypal() -> RecordingPayPal:
    return RecordingPayPal()


@pytest.fixture
def checkout(db, paypal, rulebook, clock, settings) -> CheckoutService:
    return CheckoutService(db, paypal, paypal, AddressChecker(), rulebook, clock, settings)


def _country_rows(db, order_id):
    return list(
        db.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == order_id, AuditLog.action == "checkout.country_from_paypal"
            )
        )
    )


def test_R24_paypals_shipping_country_replaces_the_declared_one(db, checkout, paypal, clock):
    order, started = _started(db, checkout)  # declared GB
    paypal.approve(started.paypal_order_id, SAM_FIXED.model_copy(update={"country": "us"}))
    checkout.on_approved(order)
    assert order.buyer_country == "US"
    [row] = _country_rows(db, order.id)
    assert (row.after, row.at) == ({"declared": "GB", "paypal": "US"}, clock.now())


def test_R24_a_matching_country_changes_nothing(db, checkout, paypal):
    order, started = _started(db, checkout)
    paypal.approve(started.paypal_order_id, SAM_FIXED)  # GB
    checkout.on_approved(order)
    assert order.buyer_country == "GB"
    assert _country_rows(db, order.id) == []


def test_R24_a_corrected_address_also_sets_the_country(db, checkout, paypal):
    order, started = _started(db, checkout)
    paypal.approve(started.paypal_order_id, SAM_FIXED)
    checkout.on_approved(order)
    us = SAM_FIXED.model_copy(
        update={
            "line1": "Apt 4, 100 Congress Ave",
            "city": "Austin",
            "postal_code": "78701",
            "country": "US",
        }
    )
    assert checkout.confirm_address(order, us).valid
    assert order.buyer_country == "US"


def test_R24_an_unusable_country_keeps_the_declared_one(db, checkout, paypal):
    order, started = _started(db, checkout)
    paypal.approve(started.paypal_order_id, SAM_FIXED.model_copy(update={"country": " "}))
    checkout.on_approved(order)
    assert order.buyer_country == "GB"
