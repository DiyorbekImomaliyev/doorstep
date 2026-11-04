"""Checkout add-on (CK-01..06): delivery choice, address check after approval, PATCH, capture.

Flow: quote -> start (PayPal order with Home / Locker / Signature options) -> buyer approves in
PayPal -> on_approved (address check) -> confirm_address (PATCH) or keep_address (override) ->
capture job. Capture never runs on an unvalidated address unless the buyer kept it (CK-06).
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.cases.engine import AddressCheck
from app.checkout import risk
from app.checkout.address import AddressChecker, AddressCheckResult
from app.checkout.ports import ApprovedOrderReader
from app.clock import Clock
from app.config import Settings
from app.money import Money
from app.orders.enums import OrderState, ShippingMethod
from app.orders.models import AuditLog, Order, Seller
from app.orders.schemas import Address
from app.paypal_client.idempotency import request_id_for
from app.paypal_client.types import ShippingOption
from app.policy.rules import RuleBook
from app.worker.gateway import OrdersGateway
from app.worker.handlers import CAPTURE_ORDER
from app.worker.runner import enqueue
from tuning import engine_config as cfg

_LABELS = {
    ShippingMethod.HOME: "Home delivery",
    ShippingMethod.LOCKER: "Parcel locker (collect with a code)",
    ShippingMethod.SIGNATURE: "Home delivery with signature",
}


class CheckoutError(Exception):
    """A checkout step that cannot run in the order's current state (API answers 409/404)."""


class DeliveryQuote(BaseModel):
    options: list[ShippingOption]
    recommended: ShippingMethod
    risk: float
    reasons: list[str]


class StartedCheckout(BaseModel):
    order_id: str
    paypal_order_id: str
    approve_url: str
    recommended: ShippingMethod


def shipping_options(amount: Money) -> list[ShippingOption]:
    """Home, locker, signature. Prices are zero (ADR: avoids an amount-breakdown mismatch)."""
    return [
        ShippingOption(method=m, label=_LABELS[m], amount=amount.zero()) for m in ShippingMethod
    ]


class CheckoutService:
    def __init__(
        self,
        session: Session,
        orders: OrdersGateway,
        reader: ApprovedOrderReader,
        checker: AddressChecker,
        rulebook: RuleBook,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self.session = session
        self.orders = orders
        self.reader = reader
        self.checker = checker
        self.rulebook = rulebook
        self.clock = clock
        self.settings = settings

    # ---- CK-03 ------------------------------------------------------------------
    def quote(self, seller_id: str, amount: Money, *, buyer_country: str) -> DeliveryQuote:
        options = shipping_options(amount)
        estimate = risk.estimate(self.session, seller_id, amount)
        reasons = list(estimate.reasons)
        sp = self.rulebook.seller_protection
        if sp.signature_recommended(amount):
            recommended = ShippingMethod.SIGNATURE
            reasons.insert(0, f"{sp.id}: signature recommended for orders over this value")
        elif estimate.p_failure >= cfg.LOCKER_RISK_THRESHOLD:
            recommended = ShippingMethod.LOCKER
            reasons.insert(
                0, f"delivery risk {estimate.p_failure:.1%}: a locker needs your pickup code"
            )
        else:
            recommended = ShippingMethod.HOME
            reasons.insert(0, f"delivery risk {estimate.p_failure:.1%} is low")
        return DeliveryQuote(
            options=options, recommended=recommended, risk=estimate.p_failure, reasons=reasons
        )

    # ---- CK-04 / CK-05 ------------------------------------------------------------
    def start(
        self,
        seller_id: str,
        amount: Money,
        method: ShippingMethod,
        *,
        buyer_country: str,
        item_description: str = "",
        scratch_tag: str | None = None,
    ) -> StartedCheckout:
        if self.session.get(Seller, seller_id) is None:
            raise CheckoutError(f"unknown seller {seller_id}")
        quote = self.quote(seller_id, amount, buyer_country=buyer_country)
        order = Order(
            seller_id=seller_id,
            amount_minor=amount.amount_minor,
            currency=amount.currency.value,
            buyer_country=buyer_country.upper(),
            shipping_method=method.value,
            recommended_method=quote.recommended.value,
            risk_score=quote.risk,
            item_description=item_description[:200],
            scratch_tag=scratch_tag,
            state=OrderState.CREATED.value,
        )
        self.session.add(order)
        self.session.flush()
        created = self.orders.create_order(
            amount=amount,
            options=quote.options,
            selected=method,
            request_id=request_id_for("order", order.id, "create"),
        )
        order.paypal_order_id = created.order_id
        self._audit(order, "checkout.started", {"method": method.value, "risk": quote.risk})
        self.session.flush()
        return StartedCheckout(
            order_id=order.id,
            paypal_order_id=created.order_id,
            approve_url=created.approve_url,
            recommended=quote.recommended,
        )

    # ---- CK-01 / CK-02 / CK-06 -------------------------------------------------------
    def on_approved(self, order: Order) -> AddressCheckResult:
        """The buyer approved in PayPal: read the address PayPal returned and check it."""
        self._require_created(order)
        approved = self.reader.get_approved(self._paypal_id(order))
        order.payer_id = approved.payer_id
        order.shipping_address = approved.address.model_dump()
        result = self.checker.check(approved.address)
        self._record(order, result)
        if result.valid:
            self.request_capture(order)
        return result

    def confirm_address(self, order: Order, address: Address) -> AddressCheckResult:
        """The buyer fixed the address: re-check, PATCH it onto the PayPal order, capture."""
        self._require_created(order)
        result = self.checker.check(address)
        if not result.valid:
            self._record(order, result)
            return result
        digest = hashlib.sha256(address.model_dump_json().encode("utf-8")).hexdigest()[:12]
        self.orders.update_shipping_address(
            self._paypal_id(order),
            address,
            request_id=request_id_for("order", order.id, "patch_address", digest),
        )
        order.shipping_address = address.model_dump()
        self._record(order, result)
        self.request_capture(order)
        return result

    def keep_address(self, order: Order) -> None:
        """The buyer keeps the original address: stored as a signal, capture proceeds (CK-02)."""
        self._require_created(order)
        order.address_override = True
        order.address_check = AddressCheck.OVERRIDDEN.value
        self._audit(order, "checkout.address_kept", {"issues": order.address_issues})
        self.request_capture(order)

    def request_capture(self, order: Order) -> None:
        if order.address_check not in (AddressCheck.VALID.value, AddressCheck.OVERRIDDEN.value):
            raise CheckoutError(f"order {order.id}: address not validated; capture refused")
        enqueue(
            self.session,
            CAPTURE_ORDER,
            {"order_id": order.id},
            clock=self.clock,
            dedupe_key=f"capture:{order.id}",
        )

    # ---- helpers --------------------------------------------------------------------
    def _record(self, order: Order, result: AddressCheckResult) -> None:
        order.address_check = (AddressCheck.VALID if result.valid else AddressCheck.INVALID).value
        order.address_issues = [i.model_dump() for i in result.issues]
        self._audit(order, "checkout.address_checked", {"valid": result.valid})
        self.session.flush()

    @staticmethod
    def _paypal_id(order: Order) -> str:
        if not order.paypal_order_id:
            raise CheckoutError(f"order {order.id} has no PayPal order")
        return order.paypal_order_id

    @staticmethod
    def _require_created(order: Order) -> None:
        if order.state != OrderState.CREATED.value:
            raise CheckoutError(f"order {order.id} is {order.state}, not awaiting payment")

    def _audit(self, order: Order, action: str, after: dict[str, object]) -> None:
        self.session.add(
            AuditLog(
                actor="checkout",
                action=action,
                entity_type="order",
                entity_id=order.id,
                before={},
                after=after,
                at=self.clock.now(),
            )
        )
