"""Order writes: create, address PATCH, capture, tracking. Importable by app.policy/app.worker.

Bodies are built with the generated SDK's models (paypal-plan.md contract sheet) and sent through
PayPalClient, so every write carries a PayPal-Request-Id (PP-05).
"""

from __future__ import annotations

from collections.abc import Sequence

from paypal.core import UnsetType
from paypal.models import Address as SdkAddress
from paypal.models import (
    AmountWithBreakdown,
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
from paypal.models import ShippingOption as SdkShippingOption
from paypal.models.enums import (
    CheckoutPaymentIntent,
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
from app.paypal_client.types import CaptureResult, CreatedOrder, ShippingOption

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


def create_order(
    client: PayPalClient,
    *,
    amount: Money,
    options: Sequence[ShippingOption],
    selected: ShippingMethod,
    request_id: str,
    return_url: str | None = None,
    cancel_url: str | None = None,
) -> CreatedOrder:
    """With return_url, PayPal sends the buyer back to Doorstep after approval (not capture:
    the address check runs first, so user_action is CONTINUE)."""
    if not any(o.method == selected for o in options):
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
    request = OrderRequest(
        intent=CheckoutPaymentIntent.CAPTURE,
        purchase_units=[
            PurchaseUnitRequest(
                amount=AmountWithBreakdown(
                    currency_code=amount.currency.value, value=amount.to_paypal_value()
                ),
                shipping=ShippingDetails(options=[_shipping_option(o, selected) for o in options]),
            )
        ],
    )
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
