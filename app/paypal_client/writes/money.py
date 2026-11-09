"""Refund and dispute writes. ONLY app.policy may import this module (PO-01, hard rule 1)."""

from __future__ import annotations

import json

from paypal.models import Refund as SdkRefund
from paypal.models import RefundRequest, RefundRequestDict

from app.money import Money
from app.paypal_client.client import PayPalClient
from app.paypal_client.errors import PayPalServerError
from app.paypal_client.sdk_models import text
from app.paypal_client.types import DisputeEvidence, RefundResult

PREFER_FULL = {"Prefer": "return=representation"}


def refund_capture(
    client: PayPalClient,
    capture_id: str,
    *,
    amount: Money | None,
    request_id: str,
    custom_id: str | None = None,
) -> RefundResult:
    """amount=None is a full refund. custom_id carries our money_action id, so a landed refund
    can be recognised after a crash.
    4xx -> PayPalClientError; 5xx/timeout -> PayPalServerError."""
    fields: RefundRequestDict = {}
    if custom_id is not None:
        fields["custom_id"] = custom_id
    if amount is not None:
        fields["amount"] = {
            "currency_code": amount.currency.value,
            "value": amount.to_paypal_value(),
        }
    request = RefundRequest.model_validate(fields)
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
        carrier = evidence.carrier or "OTHER"
        row = {"carrier_name": carrier, "tracking_number": evidence.tracking_number}
        if carrier == "OTHER":  # DE-04: PayPal requires the carrier's name with OTHER
            row["carrier_name_other"] = evidence.carrier_other or "Other carrier"
        info["tracking_info"] = [row]
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


OFFER_NOTE_MAX = 2000  # PayPal's limit on the make-offer note


def make_dispute_offer(
    client: PayPalClient, dispute_id: str, *, offer: Money, note: str, request_id: str
) -> None:
    """POST make-offer with offer_type REFUND (PP-09). PayPal allows it only in the INQUIRY stage
    and answers 200 with links only. Its docs do not say PayPal-Request-Id dedupes this call, so
    app.policy.offers keeps its own claim; the header is still sent, as on every write.
    4xx -> PayPalClientError; 5xx/timeout -> PayPalServerError (the offer may exist)."""
    if not 0 < len(note) <= OFFER_NOTE_MAX:
        raise ValueError(f"make-offer note must be 1..{OFFER_NOTE_MAX} characters")
    client.request(
        "POST",
        f"/v1/customer/disputes/{dispute_id}/make-offer",
        json={
            "note": note,
            "offer_type": "REFUND",
            "offer_amount": {
                "currency_code": offer.currency.value,
                "value": offer.to_paypal_value(),
            },
        },
        request_id=request_id,
    )
