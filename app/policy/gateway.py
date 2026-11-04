"""Real RefundGateway over app.paypal_client (reads + writes.money). Only app.policy imports it."""

from __future__ import annotations

from app.money import Money
from app.paypal_client.client import PayPalClient
from app.paypal_client.types import CaptureState, DisputeEvidence, RefundResult
from app.paypal_client.writes import money as money_writes  # noqa: F401 - only app.policy may


class RestRefundGateway:
    def __init__(self, client: PayPalClient) -> None:
        self.client = client

    def get_capture_state(self, paypal_order_id: str, capture_id: str) -> CaptureState:
        raise NotImplementedError

    def refund_capture(
        self, capture_id: str, *, amount: Money | None, request_id: str
    ) -> RefundResult:
        raise NotImplementedError

    def provide_dispute_evidence(
        self, dispute_id: str, *, evidence: DisputeEvidence, request_id: str
    ) -> None:
        raise NotImplementedError
