"""What the policy service needs from PayPal. Real implementation: app/policy/gateway.py."""

from __future__ import annotations

from typing import Protocol

from app.money import Money
from app.paypal_client.types import CaptureState, DisputeEvidence, RefundResult


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
