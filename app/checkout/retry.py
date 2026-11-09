"""CK-09: after PayPal declines the buyer's payment method, they pay another way.

PayPal's guidance for INSTRUMENT_DECLINED is to restart the payment so the buyer can choose a
different payment option, never to retry the declined one. Doorstep restarts with a new PayPal
order for the same item at the price the shop charges (CK-07), so the new order has its own
PayPal-Request-Id and nothing depends on reusing the declined one.

Only a DECLINED capture qualifies: PayPal said no money moved. A capture that failed for another
reason may hide a payment, so it is never restarted here. The declined order keeps a link to the
new one, so a second tap returns the same new order instead of creating another.
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases.engine import AddressCheck
from app.checkout.catalog import UnknownItem, charge_for
from app.checkout.service import CheckoutError, CheckoutService
from app.money import Currency
from app.orders.enums import OrderState, ShippingMethod
from app.orders.models import AuditLog, Order, Product

PAID_ANOTHER_WAY = "checkout.paid_another_way"
DECLINED = "DECLINED"


class NotDeclined(CheckoutError):
    """Only a payment PayPal declined is restarted."""


class Restarted(BaseModel):
    order_id: str
    # PayPal's approval page while the buyer has not approved the new order yet; None after.
    approve_url: str | None


def can_pay_again(order: Order) -> bool:
    return order.state == OrderState.CREATED.value and order.capture_status == DECLINED


def _earlier(session: Session, order: Order) -> Restarted | None:
    link = session.scalars(
        select(AuditLog).where(AuditLog.entity_id == order.id, AuditLog.action == PAID_ANOTHER_WAY)
    ).first()
    if link is None:
        return None
    new = session.get_one(Order, str(link.after["order_id"]))
    waiting = (
        new.state == OrderState.CREATED.value and new.address_check == AddressCheck.UNKNOWN.value
    )
    return Restarted(
        order_id=new.id, approve_url=str(link.after["approve_url"]) if waiting else None
    )


def pay_another_way(session: Session, checkout: CheckoutService, order: Order) -> Restarted:
    """A new PayPal order for the item PayPal declined to pay for. Once per declined order."""
    earlier = _earlier(session, order)
    if earlier is not None:
        return earlier
    if not can_pay_again(order):
        raise NotDeclined("only a payment PayPal declined can be paid another way")
    items = session.scalars(
        select(Product).where(
            Product.seller_id == order.seller_id, Product.name == order.item_description
        )
    ).all()
    if len(items) != 1:
        raise UnknownItem("this item is not sold here any more; order again from the shop")
    item, price = charge_for(
        session,
        order.seller_id,
        sku=items[0].sku,
        currency=Currency(order.currency),
        shown_minor=order.amount_minor,
    )
    started = checkout.start(
        order.seller_id,
        price,
        ShippingMethod(order.shipping_method or ShippingMethod.HOME.value),
        buyer_country=order.buyer_country,
        item_description=item.name,
        scratch_tag=order.scratch_tag,
        sku=item.sku,
    )
    session.add(
        AuditLog(
            actor="buyer",
            action=PAID_ANOTHER_WAY,
            entity_type="order",
            entity_id=order.id,
            before={"capture_status": DECLINED},
            after={"order_id": started.order_id, "approve_url": started.approve_url},
            at=checkout.clock.now(),
        )
    )
    session.flush()
    return Restarted(order_id=started.order_id, approve_url=started.approve_url)
