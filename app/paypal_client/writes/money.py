"""Refund and dispute writes. ONLY app.policy may import this module (PO-01, hard rule 1)."""

from __future__ import annotations

from app.money import Money
from app.paypal_client.client import PayPalClient
from app.paypal_client.types import DisputeEvidence, RefundResult


def refund_capture(
    client: PayPalClient, capture_id: str, *, amount: Money | None, request_id: str
) -> RefundResult:
    """amount=None is a full refund. 4xx -> PayPalClientError; 5xx/timeout -> PayPalServerError."""
    raise NotImplementedError


def provide_dispute_evidence(
    client: PayPalClient, dispute_id: str, *, evidence: DisputeEvidence, request_id: str
) -> None:
    raise NotImplementedError


def make_dispute_offer(client: PayPalClient, dispute_id: str, *, request_id: str) -> None:
    """Not built in the hackathon (ADR-009)."""
    raise NotImplementedError("make-offer is a documented limitation (PP-09, ADR-009)")
