"""CK-01..06: address check after approval, delivery choice, PATCH before capture."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.checkout.address import AddressChecker
from app.checkout.service import CheckoutError, CheckoutService
from app.money import Money
from app.orders.enums import ShippingMethod
from app.orders.models import Order
from app.worker.handlers import CAPTURE_ORDER
from app.worker.models import Job
from tests.factories import make_seller
from tests.fakes_journey import SAM_FIXED, SAM_NO_FLAT, FakeShippo, RecordingPayPal
from tests.helpers import money


@pytest.fixture
def paypal() -> RecordingPayPal:
    return RecordingPayPal()


@pytest.fixture
def checkout(db, paypal, rulebook, clock, settings) -> CheckoutService:
    return CheckoutService(db, paypal, paypal, AddressChecker(), rulebook, clock, settings)


def _capture_jobs(db) -> list[Job]:
    return list(db.scalars(select(Job).where(Job.kind == CAPTURE_ORDER)))


def _started(db, checkout, method=ShippingMethod.LOCKER, amount=6000):
    seller = make_seller(db)
    started = checkout.start(seller.id, money(amount), method, buyer_country="GB")
    return db.get(Order, started.order_id), started


# ---------------------------------------------------------------- CK-01 address check


def test_CK_01_building_without_flat_number_is_flagged():
    result = AddressChecker().check(SAM_NO_FLAT)
    assert result.valid is False
    assert [i.code for i in result.issues] == ["missing_unit"]
    assert "flat" in result.issues[0].message.lower()


def test_CK_01_address_with_flat_number_passes():
    assert AddressChecker().check(SAM_FIXED).valid is True


@pytest.mark.parametrize(
    ("postcode", "country", "ok"),
    [("N1 9GU", "GB", True), ("N19", "GB", False), ("94107", "US", True), ("9410", "US", False)],
)
def test_CK_01_postcode_format_is_checked(postcode, country, ok):
    address = SAM_FIXED.model_copy(update={"postal_code": postcode, "country": country})
    assert AddressChecker().check(address).valid is ok


def test_CK_01_external_validator_issues_are_included():
    shippo = FakeShippo(issues=["Street not found"])
    result = AddressChecker(validator=shippo).check(SAM_FIXED)
    assert result.valid is False
    assert any(
        i.code == "carrier_validation" and "Street not found" in i.message for i in result.issues
    )


def test_CK_01_approval_with_missing_flat_prompts_and_does_not_capture(db, checkout, paypal):
    order, started = _started(db, checkout)
    paypal.approve(started.paypal_order_id, SAM_NO_FLAT)
    result = checkout.on_approved(order)
    assert result.valid is False
    assert order.address_check == "invalid"
    assert order.payer_id == "PAYER-SAM"
    assert order.shipping_address["line1"] == SAM_NO_FLAT.line1
    assert _capture_jobs(db) == []


def test_CK_01_valid_approved_address_captures_at_once(db, checkout, paypal):
    order, started = _started(db, checkout)
    paypal.approve(started.paypal_order_id, SAM_FIXED)
    assert checkout.on_approved(order).valid is True
    assert order.address_check == "valid"
    assert len(_capture_jobs(db)) == 1


# ---------------------------------------------------------------- CK-02 keep address


def test_CK_02_buyer_keeps_address_records_override_and_captures(db, checkout, paypal):
    order, started = _started(db, checkout)
    paypal.approve(started.paypal_order_id, SAM_NO_FLAT)
    checkout.on_approved(order)
    checkout.keep_address(order)
    assert order.address_override is True
    assert order.address_check == "overridden"
    assert len(_capture_jobs(db)) == 1
    assert paypal.address_calls == []  # nothing to PATCH


# ---------------------------------------------------------------- CK-03 recommendation


def test_CK_03_all_three_methods_are_offered(db, checkout):
    quote = checkout.quote(make_seller(db).id, money(6000), buyer_country="GB")
    assert [o.method for o in quote.options] == [
        ShippingMethod.HOME,
        ShippingMethod.LOCKER,
        ShippingMethod.SIGNATURE,
    ]
    assert quote.reasons


def test_CK_03_signature_recommended_over_rule_threshold(db, checkout):
    quote = checkout.quote(
        make_seller(db).id, Money.from_paypal("800.00", "USD"), buyer_country="US"
    )
    assert quote.recommended == ShippingMethod.SIGNATURE
    assert any("paypal_sp_v1" in r for r in quote.reasons)


def test_CK_03_low_value_low_risk_recommends_home(db, checkout):
    quote = checkout.quote(make_seller(db).id, money(1500), buyer_country="GB")
    assert quote.recommended == ShippingMethod.HOME


def test_CK_03_higher_risk_recommends_locker(db, checkout):
    quote = checkout.quote(make_seller(db).id, money(6000), buyer_country="GB")
    assert quote.recommended == ShippingMethod.LOCKER
    assert 0.0 <= quote.risk <= 1.0


# ---------------------------------------------------------------- CK-04 / CK-05 choice


def test_CK_04_chosen_method_is_stored_on_the_order(db, checkout):
    order, started = _started(db, checkout, method=ShippingMethod.HOME)
    assert order.shipping_method == "home"
    assert order.recommended_method == started.recommended.value
    assert order.state == "CREATED"


def test_CK_05_paypal_order_carries_the_chosen_option(db, checkout, paypal):
    order, started = _started(db, checkout, method=ShippingMethod.LOCKER)
    [created] = paypal.created
    assert created["selected"] == ShippingMethod.LOCKER
    assert {o.method for o in created["options"]} == set(ShippingMethod)
    assert created["amount"] == money(6000)
    assert order.paypal_order_id == started.paypal_order_id
    assert started.approve_url.startswith("https://")


def test_CK_05_unknown_seller_is_refused(db, checkout):
    with pytest.raises(CheckoutError):
        checkout.start("nope", money(6000), ShippingMethod.HOME, buyer_country="GB")


# ---------------------------------------------------------------- CK-06 PATCH before capture


def test_CK_06_confirmed_fix_is_patched_before_capture(db, checkout, paypal):
    order, started = _started(db, checkout)
    paypal.approve(started.paypal_order_id, SAM_NO_FLAT)
    checkout.on_approved(order)
    result = checkout.confirm_address(order, SAM_FIXED)
    assert result.valid is True
    [(ppo, address, request_id)] = paypal.address_calls
    assert (ppo, address) == (started.paypal_order_id, SAM_FIXED)
    assert request_id.startswith(f"order:{order.id}:patch_address")
    assert order.address_check == "valid"
    assert order.shipping_address["line1"] == SAM_FIXED.line1
    assert len(_capture_jobs(db)) == 1


def test_CK_06_a_fix_that_still_fails_is_not_patched_or_captured(db, checkout, paypal):
    order, started = _started(db, checkout)
    paypal.approve(started.paypal_order_id, SAM_NO_FLAT)
    checkout.on_approved(order)
    assert checkout.confirm_address(order, SAM_NO_FLAT).valid is False
    assert paypal.address_calls == []
    assert _capture_jobs(db) == []


def test_CK_06_capture_never_runs_on_an_unvalidated_address(db, checkout):
    order, _ = _started(db, checkout)
    with pytest.raises(CheckoutError):
        checkout.request_capture(order)
    assert _capture_jobs(db) == []


def test_CK_06_capture_is_requested_once(db, checkout, paypal):
    order, started = _started(db, checkout)
    paypal.approve(started.paypal_order_id, SAM_FIXED)
    checkout.on_approved(order)
    checkout.request_capture(order)  # a second request (e.g. a double click) is deduped
    assert len(_capture_jobs(db)) == 1
