"""What the policy service needs from PayPal. Real implementation: app/policy/gateway.py."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.money import Money
from app.paypal_client.types import (
    CaptureState,
    DisputeDetail,
    DisputeEvidence,
    DisputeSummary,
    RefundResult,
)


class RefundGateway(Protocol):
    def get_capture_state(self, paypal_order_id: str, capture_id: str) -> CaptureState: ...

    def refund_capture(
        self,
        capture_id: str,
        *,
        amount: Money | None,
        request_id: str,
        custom_id: str | None = None,
    ) -> RefundResult: ...

    def provide_dispute_evidence(
        self, dispute_id: str, *, evidence: DisputeEvidence, request_id: str
    ) -> None: ...


@runtime_checkable
class DisputeLookup(Protocol):
    """Live dispute read for the pre-refund re-check (webhooks are hints; PayPal decides).

    Webhooks fill the local disputes table, but a webhook can be late, so right before money
    moves the re-check asks PayPal itself. Both production gateways implement this, and a test
    says so; a gateway without it is only ever a test double.
    """

    def open_disputes_since(self, since: datetime, *, capture_id: str) -> list[DisputeSummary]: ...


@runtime_checkable
class OfferGateway(Protocol):
    """A refund offer inside an open PayPal dispute (PP-09): read the dispute, make the offer."""

    def get_dispute(self, dispute_id: str) -> DisputeDetail: ...

    def make_dispute_offer(
        self, dispute_id: str, *, offer: Money, note: str, request_id: str
    ) -> None: ...
