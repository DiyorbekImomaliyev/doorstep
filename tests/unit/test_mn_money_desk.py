"""MN-02..MN-08: the Money desk reads each PayPal write's whole story from Doorstep's own rows.

The refunds here go through the real policy path (decide -> outbox job -> re-check -> PayPal fake),
so the desk is tested against the audit rows the money path really writes, not a copy of them.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import AppDeps, Services
from app.main import create_app
from app.orders.enums import CaseState, MoneyActionStatus
from app.orders.models import Decision, MoneyAction, Recovery, Seller
from app.policy.service import REFUND_JOB, RefundJobHandler
from app.proof.receipts import ReceiptSigner, issue_refund_receipt
from app.runtime import ROOT
from app.security.links import LinkSigner
from app.worker.models import Job
from tests.factories import make_case, make_order, make_seller
from tests.unit import test_po_13_refund_status as po13

# The PO-13 fixtures: a refund queued by decide(), the PayPal fake and the refund job runner.
paypal, queued, run_refund = po13.paypal, po13.queued, po13.run_refund

SECRET = "money-desk-test-secret-0123456789"
SIGNER = ReceiptSigner.generate()
BUYER_NAME = "Buyer Name Secret"


@pytest.fixture
def api(session_factory, settings, clock) -> TestClient:
    services = Services(
        links=LinkSigner(SECRET, clock), receipts=SIGNER, orders=None, approved=None
    )
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=ROOT / "rules",
        services=services,
    )
    return TestClient(create_app(deps))


def _token(seller_id: str, clock, scope: str = "seller") -> str:
    return LinkSigner(SECRET, clock).issue(seller_id, scope=scope)


def _detail(api, clock, ma: MoneyAction) -> dict:
    res = api.get(f"/api/seller/{_token(ma.seller_id, clock)}/money-desk/{ma.id}")
    assert res.status_code == 200, res.text
    return res.json()


def test_MN_02_desk_rows_carry_simulated_receipts_and_carrier_money(
    queued, run_refund, db, clock, api
):
    order, _, ma = queued
    run_refund()
    issue_refund_receipt(db, SIGNER, ma, at=clock.now())
    seller = db.get(Seller, order.seller_id)
    assert seller is not None
    usd = make_order(db, seller, currency="USD", buyer_country="US")
    usd_case = make_case(db, usd)
    db.add_all(
        [
            MoneyAction(
                order_id=usd.id,
                case_id=usd_case.id,
                seller_id=order.seller_id,
                kind="refund",
                amount_minor=2500,
                currency="USD",
                request_id="req-sim-usd",
                status="completed",
                paypal_status="SIMULATED_COMPLETED",
                paypal_refund_id="SIM-REFUND-1",
            ),
            Recovery(
                case_id=usd_case.id,
                order_id=usd.id,
                seller_id=order.seller_id,
                decision_id="d1",
                carrier="usps",
                carrier_id="usps",
                failure_point="CARRIER_LOST",
                claim_minor=2500,
                currency="USD",
                status="PAID",
                recovered_minor=2000,
                deadline_at=clock.now() + timedelta(days=30),
                resolved_at=clock.now(),
                terms={"carrier_name": "USPS"},
            ),
        ]
    )
    db.commit()
    body = api.get(f"/api/seller/{_token(order.seller_id, clock)}/money-desk").json()
    rows = {r["request_id"]: r for r in body["rows"]}
    sent = rows[ma.request_id]
    assert sent["status"] == "completed" and not sent["simulated"]
    assert sent["receipts"] == 1 and sent["completed_at"] is not None
    sim = rows["req-sim-usd"]
    assert sim["status"] == "simulated" and sim["simulated"] and sim["currency"] == "USD"
    [paid] = body["recoveries"]
    assert (paid["recovered_minor"], paid["currency"], paid["carrier_name"]) == (
        2000,
        "USD",
        "USPS",
    )
    assert paid["paid_at"] is not None


def test_MN_02_only_paid_claims_count_as_money_back(db, clock, api):
    seller = make_seller(db)
    order = make_order(db, seller)
    case = make_case(db, order)
    db.add(
        Recovery(
            case_id=case.id,
            order_id=order.id,
            seller_id=seller.id,
            decision_id="d1",
            carrier="royal_mail",
            carrier_id="royal_mail",
            failure_point="CARRIER_LOST",
            claim_minor=4000,
            currency="GBP",
            status="SUBMITTED",
            deadline_at=clock.now() + timedelta(days=30),
        )
    )
    db.commit()
    body = api.get(f"/api/seller/{_token(seller.id, clock)}/money-desk").json()
    assert body["recoveries"] == [] and body["rows"] == []


def test_MN_05_a_sent_refund_shows_its_lifecycle_in_order(queued, run_refund, db, clock, api):
    _, _, ma = queued
    run_refund()
    issue_refund_receipt(db, SIGNER, ma, at=clock.now())
    db.commit()
    d = _detail(api, clock, ma)
    assert [s["step"] for s in d["timeline"]] == [
        "approved",
        "queued",
        "rechecked",
        "completed",
        "signed",
    ]
    approved, queued_step, rechecked, completed, signed = d["timeline"]
    assert approved["actor"] == "Doorstep policy" and approved["rule_id"] == d["rule_id"]
    assert queued_step["rule_version"] == d["rule_version"] and d["rule_version"]
    assert rechecked["detail"] == "5 checks passed"
    assert completed["actor"] == "PayPal" and completed["detail"] == ma.paypal_refund_id
    assert signed["label"].startswith("Signed · #")
    assert d["checks"] == [
        "Case still open",
        "No dispute",
        "Evidence unchanged",
        "Capture refundable",
        "Within the capture",
    ]
    assert d["request_id"] == ma.request_id and d["paypal_refund_id"] == ma.paypal_refund_id
    assert d["outbox"] == {"status": "done", "attempts": 1, "created_at": d["outbox"]["created_at"]}
    assert d["stop"] is None and d["can_redecide"] is False and d["approver"] == "Doorstep policy"


def test_MN_05_a_blocked_refund_says_which_check_stopped_it(queued, run_refund, db, clock, api):
    _, case, ma = queued
    decision = db.get(Decision, ma.decision_id)
    assert decision is not None
    decision.evidence_fingerprint = "changed-since"
    run_refund()
    assert ma.status == MoneyActionStatus.BLOCKED.value
    d = _detail(api, clock, ma)
    assert d["stop"] == {
        "title": "Stopped before PayPal",
        "text": "Evidence changed since the decision. No money moved.",
        "check": "Evidence unchanged",
    }
    assert d["timeline"][-1]["step"] == "blocked"
    assert d["timeline"][-1]["detail"] == "Evidence changed since the decision"
    assert d["checks"] == [] and d["paypal_refund_id"] is None
    assert case.state == CaseState.HOLD_FOR_REVIEW.value and d["can_redecide"] is True


def test_MN_05_paypal_declining_a_refund_is_said_plainly(
    queued, run_refund, paypal, db, clock, api
):
    _, _, ma = queued
    paypal.answer = "FAILED"
    run_refund()
    d = _detail(api, clock, ma)
    assert d["stop"]["title"] == "PayPal declined"
    assert "FAILED" in d["stop"]["text"]
    assert [s["step"] for s in d["timeline"]][-2:] == ["rechecked", "failed"]
    assert d["timeline"][-1]["label"] == "PayPal: failed"


def test_MN_05_a_refund_that_timed_out_names_the_same_key(queued, db, clock, api, rulebook):
    _, _, ma = queued
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    job.attempts = 3
    RefundJobHandler(po13.StatusPayPal(), rulebook, clock).on_failed(
        db, job, TimeoutError("timed out")
    )
    db.commit()
    d = _detail(api, clock, ma)
    assert d["stop"]["title"] == "PayPal did not answer"
    assert "same PayPal-Request-Id" in d["stop"]["text"]
    assert d["outbox"]["attempts"] == 3 and d["checks"] == []
    assert d["timeline"][-1]["label"] == "Not sent"
    assert "TimeoutError" not in json.dumps(d)


def test_MN_06_the_receipt_comes_whole_and_verifies(queued, run_refund, db, clock, api):
    _, case, ma = queued
    run_refund()
    issue_refund_receipt(db, SIGNER, ma, at=clock.now())
    db.commit()
    d = _detail(api, clock, ma)
    [receipt] = d["receipts"]
    assert receipt["kind"] == "refund" and receipt["paid"] is True
    assert d["public_key"] == SIGNER.public_key_b64
    assert SIGNER.verify(receipt["hash"], receipt["signature"])
    check = api.get(f"/cases/{case.id}/verify").json()
    assert check["status"] == "ok"
    assert {"seq": receipt["seq"], "kind": "refund", "hash": receipt["hash"], "valid": True} in (
        check["receipts"]
    )


def test_MN_08_the_desk_is_seller_scoped(queued, db, clock, api):
    order, _, ma = queued
    stranger = make_seller(db)
    other_order = make_order(db, stranger)
    db.commit()
    mine = _token(order.seller_id, clock)
    theirs = _token(stranger.id, clock)
    buyer = _token(other_order.id, clock, scope="buyer")
    assert api.get(f"/api/seller/{theirs}/money-desk").json()["rows"] == []
    assert api.get(f"/api/seller/{theirs}/money-desk/{ma.id}").status_code == 404
    assert api.get(f"/api/seller/{mine}/money-desk/no-such-action").status_code == 404
    for bad in (buyer, "not-a-token"):
        assert api.get(f"/api/seller/{bad}/money-desk").status_code == 404
        assert api.get(f"/api/seller/{bad}/money-desk/{ma.id}").status_code == 404


def test_MN_08_no_personal_data_or_ids_of_people(queued, run_refund, db, clock, api):
    order, _, ma = queued
    order.shipping_address = {"name": BUYER_NAME, "line1": "1 Secret Road", "postal_code": "N1 9GU"}
    run_refund()
    body = json.dumps(
        [
            api.get(f"/api/seller/{_token(order.seller_id, clock)}/money-desk").json(),
            _detail(api, clock, ma),
        ]
    )
    for secret in (BUYER_NAME, "Secret Road", "N1 9GU", "@", order.payer_id or "PAYER"):
        assert secret not in body
