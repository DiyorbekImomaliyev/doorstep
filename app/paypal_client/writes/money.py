"""Refund and dispute writes. ONLY app.policy may import this module (PO-01, hard rule 1)."""

from __future__ import annotations

import json

from paypal.models import Money as SdkMoney
from paypal.models import Refund as SdkRefund
from paypal.models import RefundRequest

from app.money import Money
from app.paypal_client.client import PayPalClient
from app.paypal_client.errors import PayPalServerError
from app.paypal_client.sdk_models import text
from app.paypal_client.types import DisputeEvidence, RefundResult

PREFER_FULL = {"Prefer": "return=representation"}


def refund_capture(
    client: PayPalClient, capture_id: str, *, amount: Money | None, request_id: str
) -> RefundResult:
    """amount=None is a full refund. 4xx -> PayPalClientError; 5xx/timeout -> PayPalServerError."""
    request = (
        RefundRequest()
        if amount is None
        else RefundRequest(
            amount=SdkMoney(currency_code=amount.currency.value, value=amount.to_paypal_value())
        )
    )
    body = client.request(
        "POST",
        f"/v2/payments/captures/{capture_id}/refund",
        json=request.to_dict(),
        request_id=request_id,
        headers=PREFER_FULL,
    )
    refund = SdkRefund.model_validate(body)
    refund_id = text(refund.id)
    if not refund_id:  # the refund may have happened: the job retries with the SAME key
        raise PayPalServerError(None, "refund response had no id; outcome unknown")
    return RefundResult(refund_id=refund_id, status=text(refund.status) or "UNKNOWN", raw=body)


def provide_dispute_evidence(
    client: PayPalClient, dispute_id: str, *, evidence: DisputeEvidence, request_id: str
) -> None:
    """POST provide-evidence as multipart/form-data with the JSON `input` part (PP-09)."""
    info: dict[str, object] = {}
    if evidence.tracking_number:
        info["tracking_info"] = [
            {
                "carrier_name": evidence.carrier or "OTHER",
                "tracking_number": evidence.tracking_number,
            }
        ]
    payload = {
        "evidences": [
            {
                "evidence_type": evidence.evidence_type,
                "evidence_info": info,
                "notes": evidence.notes,
            }
        ]
    }
    client.request(
        "POST",
        f"/v1/customer/disputes/{dispute_id}/provide-evidence",
        files={"input": (None, json.dumps(payload), "application/json")},
        request_id=request_id,
    )


def make_dispute_offer(client: PayPalClient, dispute_id: str, *, request_id: str) -> None:
    """Not built in the hackathon (ADR-009)."""
    raise NotImplementedError("make-offer is a documented limitation (PP-09, ADR-009)")
