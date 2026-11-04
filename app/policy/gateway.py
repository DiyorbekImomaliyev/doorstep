"""Real RefundGateway over app.paypal_client (reads + writes.money). Only app.policy imports it."""

from __future__ import annotations

from app.money import Money
from app.paypal_client import reads
from app.paypal_client.client import PayPalClient
from app.paypal_client.types import CaptureState, DisputeEvidence, RefundResult
from app.paypal_client.writes import money as money_writes


class RestRefundGateway:
    def __init__(self, client: PayPalClient) -> None:
        self.client = client

    def get_capture_state(self, paypal_order_id: str, capture_id: str) -> CaptureState:
        return reads.get_capture_state(self.client, paypal_order_id, capture_id)

    def refund_capture(
        self,
        capture_id: str,
        *,
        amount: Money | None,
        request_id: str,
        custom_id: str | None = None,
    ) -> RefundResult:
        return money_writes.refund_capture(
            self.client, capture_id, amount=amount, request_id=request_id, custom_id=custom_id
        )

    def provide_dispute_evidence(
        self, dispute_id: str, *, evidence: DisputeEvidence, request_id: str
    ) -> None:
        money_writes.provide_dispute_evidence(
            self.client, dispute_id, evidence=evidence, request_id=request_id
        )
