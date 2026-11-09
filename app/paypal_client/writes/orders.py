"""Order writes: create, address PATCH, capture, tracking. Importable by app.policy/app.worker.

Bodies are built with the generated SDK's models and sent
through PayPalClient, so every write carries a PayPal-Request-Id (PP-05).
"""

from __future__ import annotations

from collections.abc import Sequence

from paypal.core import UnsetType
from paypal.models import Address as SdkAddress
from paypal.models import (
    AmountBreakdown,
    AmountWithBreakdown,
    ItemRequest,
    OrderRequest,
    OrderTrackerRequest,
    Patch,
    PaymentSource,
    PayPalWallet,
    PayPalWalletExperienceContext,
    PurchaseUnitRequest,
    ShippingDetails,
    ShippingOptionDict,
)
from paypal.models import Money as SdkMoney
from paypal.models import ShippingOption as SdkShippingOption
from paypal.models.enums import (
    CheckoutPaymentIntent,
    ItemCategory,
    PatchOp,
    PayPalExperienceUserAction,
    PayPalWalletContextShippingPreference,
    ShippingType,
)

from app.money import Money
from app.orders.enums import ShippingMethod
from app.orders.schemas import Address
from app.paypal_client.client import PayPalClient
from app.paypal_client.errors import PayPalClientError, PayPalServerError
from app.paypal_client.sdk_models import (
    approve_link,
    captures,
    decode_order,
    money_from_sdk,
    text,
    texts,
)
from app.paypal_client.types import CaptureResult, CreatedOrder, OrderItem, ShippingOption

PREFER_FULL = {"Prefer": "return=representation"}
# Locker delivery is a pickup; home and signature are shipped to the buyer.
_OPTION_TYPE = {
    ShippingMethod.HOME: ShippingType.SHIPPING,
    ShippingMethod.LOCKER: ShippingType.PICKUP,
    ShippingMethod.SIGNATURE: ShippingType.SHIPPING,
}
SHIPPING_ADDRESS_PATH = "/purchase_units/@reference_id=='default'/shipping/address"


def _shipping_option(option: ShippingOption, selected: ShippingMethod) -> SdkShippingOption:
    # The companion dict is keyed by Python names (type_ is sent as "type").
    spec: ShippingOptionDict = {
        "id": option.method.value,
        "label": option.label,
        "type_": _OPTION_TYPE[option.method],
        "amount": {
            "currency_code": option.amount.currency.value,
            "value": option.amount.to_paypal_value(),
        },
        "selected": option.method == selected,
    }
    return SdkShippingOption.model_validate(spec)


_TEXT_MAX = 127  # PayPal's limit for item name and sku, invoice_id and custom_id


def _sdk_money(money: Money) -> SdkMoney:
    return SdkMoney(currency_code=money.currency.value, value=money.to_paypal_value())


def _itemised(
    amount: Money, item: OrderItem, shipping: Money
) -> tuple[ItemRequest, AmountBreakdown]:
    """The item and a breakdown that adds up to the order amount, or ValueError: PayPal refuses
    a breakdown that does not, so a mismatch is caught here before anything is sent."""
    total = item.unit.amount_minor * item.quantity
    same_currency = item.unit.currency == amount.currency == shipping.currency
    if not same_currency or total + shipping.amount_minor != amount.amount_minor:
        raise ValueError(
            f"item total {item.quantity} x {item.unit} + shipping {shipping} is not {amount}"
        )
    line = ItemRequest(
        name=item.name[:_TEXT_MAX],
        sku=item.sku[:_TEXT_MAX],
        quantity=str(item.quantity),
        unit_amount=_sdk_money(item.unit),
        category=ItemCategory.PHYSICAL_GOODS,
    )
    item_total = Money(amount_minor=total, currency=amount.currency)
    return line, AmountBreakdown(item_total=_sdk_money(item_total), shipping=_sdk_money(shipping))


def create_order(
    client: PayPalClient,
    *,
    amount: Money,
    options: Sequence[ShippingOption],
    selected: ShippingMethod,
    request_id: str,
    return_url: str | None = None,
    cancel_url: str | None = None,
    item: OrderItem | None = None,
    invoice_id: str | None = None,
    custom_id: str | None = None,
) -> CreatedOrder:
    """With return_url, PayPal sends the buyer back to Doorstep after approval (not capture:
    the address check runs first, so user_action is CONTINUE). CK-10: `item` lists what was
    bought, `invoice_id` and `custom_id` tie the PayPal order to Doorstep's."""
    chosen = [o for o in options if o.method == selected]
    if not chosen:
        raise ValueError(f"selected method {selected} is not one of the options")
    source = None
    if return_url:
        source = PaymentSource(
            paypal=PayPalWallet(
                experience_context=PayPalWalletExperienceContext(
                    return_url=return_url,
                    cancel_url=cancel_url or return_url,
                    shipping_preference=PayPalWalletContextShippingPreference.GET_FROM_FILE,
                    user_action=PayPalExperienceUserAction.CONTINUE,
                )
            )
        )
    unit = PurchaseUnitRequest(
        amount=AmountWithBreakdown(
            currency_code=amount.currency.value, value=amount.to_paypal_value()
        ),
        shipping=ShippingDetails(options=[_shipping_option(o, selected) for o in options]),
    )
    if item is not None:
        line, breakdown = _itemised(amount, item, chosen[0].amount)
        unit = unit.model_copy(
            update={
                "items": [line],
                "amount": unit.amount.model_copy(update={"breakdown": breakdown}),
            }
        )
    if invoice_id:
        unit = unit.model_copy(update={"invoice_id": invoice_id[:_TEXT_MAX]})
    if custom_id:
        unit = unit.model_copy(update={"custom_id": custom_id[:_TEXT_MAX]})
    request = OrderRequest(intent=CheckoutPaymentIntent.CAPTURE, purchase_units=[unit])
    if source is not None:
        request = request.model_copy(update={"payment_source": source})
    body = request.to_dict()
    response = client.request(
        "POST", "/v2/checkout/orders", json=body, request_id=request_id, headers=PREFER_FULL
    )
    order = decode_order(response)
    order_id, link = text(order.id), approve_link(order)
    if not order_id or not link:  # the order may exist: retry with the same key to read it
        raise PayPalServerError(None, "create order response had no id or approve link")
    return CreatedOrder(order_id=order_id, approve_url=link)


def update_shipping_address(
    client: PayPalClient, paypal_order_id: str, address: Address, *, request_id: str
) -> None:
    """PATCH the buyer-confirmed address onto the order, after approval and before capture."""
    value = SdkAddress(
        address_line_1=address.line1,
        admin_area_2=address.city,
        postal_code=address.postal_code,
        country_code=address.country.upper(),
        **({"address_line_2": address.line2} if address.line2 else {}),
    ).to_dict()
    patch = Patch(op=PatchOp.REPLACE, path=SHIPPING_ADDRESS_PATH, value=value).to_dict()
    client.request(
        "PATCH", f"/v2/checkout/orders/{paypal_order_id}", json=[patch], request_id=request_id
    )


def capture_order(client: PayPalClient, paypal_order_id: str, *, request_id: str) -> CaptureResult:
    """422 INSTRUMENT_DECLINED returns CaptureResult(status="DECLINED")."""
    try:
        response = client.request(
            "POST",
            f"/v2/checkout/orders/{paypal_order_id}/capture",
            json={},
            request_id=request_id,
            headers=PREFER_FULL,
        )
    except PayPalClientError as err:
        details = err.body.get("details")
        issues = {
            str(d.get("issue"))
            for d in (details if isinstance(details, list) else [])
            if isinstance(d, dict)
        }
        if err.status == 422 and "INSTRUMENT_DECLINED" in issues:
            return CaptureResult(status="DECLINED")
        raise
    capture = next(iter(captures(decode_order(response))), None)
    if capture is None:
        return CaptureResult(status=text(decode_order(response).status) or "UNKNOWN")
    protection = capture.seller_protection
    protected = not isinstance(protection, UnsetType)
    return CaptureResult(
        status=text(capture.status) or "UNKNOWN",
        capture_id=text(capture.id),
        amount=money_from_sdk(capture.amount),
        seller_protection=text(protection.status) if protected else None,
        protected_categories=texts(protection.dispute_categories) if protected else [],
    )


def add_tracking(
    client: PayPalClient,
    *,
    paypal_order_id: str,
    capture_id: str,
    carrier: str,
    tracking_number: str,
    request_id: str,
    carrier_name_other: str | None = None,
) -> None:
    if carrier == "OTHER":  # PayPal needs the carrier's name when the code is OTHER
        tracker = OrderTrackerRequest(
            capture_id=capture_id,
            tracking_number=tracking_number,
            carrier=carrier,
            carrier_name_other=carrier_name_other or "Other carrier",
            notify_payer=False,
        )
    else:
        tracker = OrderTrackerRequest(
            capture_id=capture_id,
            tracking_number=tracking_number,
            carrier=carrier,
            notify_payer=False,
        )
    body = tracker.to_dict()
    client.request(
        "POST", f"/v2/checkout/orders/{paypal_order_id}/track", json=body, request_id=request_id
    )
