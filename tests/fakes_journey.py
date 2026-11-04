"""Fakes for the buyer journey (checkout, shipping, AI). tests/fakes.py is locked; new fakes live
here. No network: every external call is recorded and answered from memory."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from app.checkout.ports import ApprovedOrder
from app.money import Money
from app.orders.enums import ShippingMethod
from app.orders.schemas import Address
from app.paypal_client.types import CaptureResult, CreatedOrder, ShippingOption
from tests.fakes import FakePayPal


class RecordingPayPal(FakePayPal):
    """FakePayPal that also records created orders and answers approved-order reads."""

    def __init__(self) -> None:
        super().__init__()
        self.created: list[dict[str, Any]] = []
        self.approved: dict[str, ApprovedOrder] = {}

    def create_order(
        self,
        *,
        amount: Money,
        options: Sequence[ShippingOption],
        selected: ShippingMethod,
        request_id: str,
    ) -> CreatedOrder:
        created = super().create_order(
            amount=amount, options=options, selected=selected, request_id=request_id
        )
        self.created.append(
            {
                "paypal_order_id": created.order_id,
                "amount": amount,
                "options": list(options),
                "selected": selected,
                "request_id": request_id,
            }
        )
        return created

    def approve(self, paypal_order_id: str, address: Address, payer_id: str = "PAYER-SAM") -> None:
        self.approved[paypal_order_id] = ApprovedOrder(
            paypal_order_id=paypal_order_id, payer_id=payer_id, address=address
        )

    def get_approved(self, paypal_order_id: str) -> ApprovedOrder:
        return self.approved[paypal_order_id]

    def will_capture(self, status: str = "COMPLETED", capture_id: str = "CAP-X") -> None:
        self.capture_results.append(CaptureResult(status=status, capture_id=capture_id))


class FakeShippo:
    def __init__(self, issues: list[str] | None = None) -> None:
        self.tracks: list[tuple[str, str]] = []
        self.validated: list[Address] = []
        self.issues = issues or []

    def register_track(self, carrier: str, tracking_number: str) -> None:
        self.tracks.append((carrier, tracking_number))

    def validate(self, address: Address) -> list[str]:
        self.validated.append(address)
        return list(self.issues)


class FakeLLM:
    """Answers `complete_structured` from a queue; records every call (no network)."""

    def __init__(self, *answers: BaseModel | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def complete_structured(
        self, *, schema: type[BaseModel], system: str, user: str, **kwargs: Any
    ) -> BaseModel:
        self.calls.append({"schema": schema, "system": system, "user": user, **kwargs})
        answer = self.answers.pop(0) if self.answers else RuntimeError("no answer queued")
        if isinstance(answer, Exception):
            raise answer
        return answer


SAM_NO_FLAT = Address(
    name="Sam Carter",
    line1="Rosewood Court, 10 High Street",
    city="London",
    postal_code="N1 9GU",
    country="GB",
)
SAM_FIXED = SAM_NO_FLAT.model_copy(update={"line1": "Flat 4, Rosewood Court, 10 High Street"})
