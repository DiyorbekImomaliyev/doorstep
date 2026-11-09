"""DE-04: evidence for a carrier PayPal has no code for names the carrier.

PayPal's disputes schema: `carrier_name_other` "is mandatory when `carrier_name` is `OTHER`". The
evidence used to send OTHER alone, so a dispute for a parcel sent with a carrier outside PayPal's
list (Shippo's test carrier, or an unlisted courier) was missing a required field. The tracking
write already named the carrier; the evidence now does the same.
"""

from __future__ import annotations

from sqlalchemy import select

from app.cases.dispute_evidence import build_submission
from app.orders.models import Decision, Shipment
from app.paypal_client.types import DisputeEvidence
from app.paypal_client.writes import money as money_writes
from app.policy.evidence import EvidenceSubmission
from tests.unit.test_de_dispute_evidence import (  # noqa: F401 - fixtures
    VERIFY,
    _disputed,
    pipeline,
    signer,
)
from tests.unit.test_pp_paypal_client import api_requests, pp, serve_token  # noqa: F401

PATH = "/v1/customer/disputes/D-1/provide-evidence"


def _tracking_sent(pp, httpserver, evidence: DisputeEvidence) -> list[dict]:  # noqa: F811
    import json

    serve_token(httpserver)
    httpserver.expect_request(PATH, method="POST").respond_with_json({"links": []}, status=200)
    money_writes.provide_dispute_evidence(pp, "D-1", evidence=evidence, request_id="r-1")
    raw = api_requests(httpserver, PATH)[-1].form["input"]
    return json.loads(raw)["evidences"][0]["evidence_info"]["tracking_info"]


def _evidence(**kw) -> DisputeEvidence:
    return DisputeEvidence(
        evidence_type="PROOF_OF_FULFILLMENT", notes="Delivered", tracking_number="T-1", **kw
    )


def test_DE_04_an_unlisted_carrier_is_sent_as_other_with_its_name(pp, httpserver):  # noqa: F811
    assert _tracking_sent(pp, httpserver, _evidence(carrier=None, carrier_other="Evri")) == [
        {"carrier_name": "OTHER", "carrier_name_other": "Evri", "tracking_number": "T-1"}
    ]


def test_DE_04_other_without_a_name_still_carries_one(pp, httpserver):  # noqa: F811
    [row] = _tracking_sent(pp, httpserver, _evidence(carrier="OTHER"))
    assert row["carrier_name"] == "OTHER" and row["carrier_name_other"] == "Other carrier"


def test_DE_04_a_listed_carrier_sends_only_its_code(pp, httpserver):  # noqa: F811
    [row] = _tracking_sent(pp, httpserver, _evidence(carrier="ROYAL_MAIL", carrier_other="x"))
    assert row == {"carrier_name": "ROYAL_MAIL", "tracking_number": "T-1"}


def test_DE_04_the_submission_names_an_unlisted_carrier(db, clock, pipeline):  # noqa: F811
    _, order, case = _disputed(db, clock, pipeline)
    decision = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    db.scalars(select(Shipment).where(Shipment.order_id == order.id)).one().carrier = "shippo"
    sub = build_submission(db, order, case, decision, VERIFY)
    assert (sub.carrier, sub.carrier_other) == (None, "shippo")
    # the job payload carries it to the PayPal write unchanged
    assert DisputeEvidence.model_validate(sub.model_dump()).carrier_other == "shippo"


def test_DE_04_a_listed_carrier_has_no_other_name(db, clock, pipeline):  # noqa: F811
    _, order, case = _disputed(db, clock, pipeline)
    decision = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    sub = build_submission(db, order, case, decision, VERIFY)
    assert sub.carrier == "USPS" and sub.carrier_other is None
    assert EvidenceSubmission.model_fields["carrier_other"].default is None
