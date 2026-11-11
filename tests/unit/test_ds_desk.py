"""DS-02 .. DS-07: the PayPal disputes desk, read only, from stored rows."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import AppDeps, Services
from app.main import create_app
from app.orders.enums import CaseState
from app.orders.models import AuditLog, Dispute, MoneyAction, TrackingEvent
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner
from app.worker.kinds import ADD_TRACKING
from app.worker.models import Job, WebhookInbox
from tests.factories import add_decision, make_case, make_order, make_seller, make_shipment

SECRET = "desk-test-secret-0123456789abcdef"
BUYER_NAME = "Buyer Name Secret"


@pytest.fixture
def api(session_factory, settings, clock) -> TestClient:
    services = Services(
        links=LinkSigner(SECRET, clock),
        receipts=ReceiptSigner.generate(),
        orders=None,
        approved=None,
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


def _token(seller, clock, scope="seller"):
    return LinkSigner(SECRET, clock).issue(seller.id if scope == "seller" else seller, scope=scope)


def _desk(api, seller, clock) -> dict[str, dict]:
    res = api.get(f"/api/seller/{_token(seller, clock)}/disputes/desk")
    assert res.status_code == 200
    return {d["paypal_dispute_id"]: d for d in res.json()}


def _hook(db, clock, event_type, resource, minutes=0):
    db.add(
        WebhookInbox(
            event_id=f"WH-{event_type}-{resource['dispute_id']}-{minutes}",
            source="paypal",
            event_type=event_type,
            payload={"resource": resource},
            status="processed",
            received_at=clock.now() + timedelta(minutes=minutes),
        )
    )


def _disputed(db, seller, *, dispute_id, amount_minor=8900, currency="USD", status="open"):
    order = make_order(db, seller, amount_minor=amount_minor, currency=currency)
    order.item_description = "Wireless headphones"
    case = make_case(db, order, state=CaseState.DISPUTE_EVIDENCE)
    decision = add_decision(db, case, route="DISPUTE_EVIDENCE")
    decision.failure_point = "STOLEN_AFTER_DELIVERY"
    decision.refund_amount_minor = amount_minor
    decision.currency = currency
    decision.reasons = ["carrier scanned the parcel as delivered"]
    decision.signals = {"scan_audit": {"verdict": "consistent", "checks": []}}
    db.add(Dispute(order_id=order.id, paypal_dispute_id=dispute_id, status=status))
    db.flush()
    return order, case, decision


def test_DS_02_desk_is_seller_scoped_and_refuses_other_links(db, clock, api):
    seller = make_seller(db)
    order, _, _ = _disputed(db, seller, dispute_id="SIM-DISPUTE-A")
    stranger = make_seller(db)
    make_order(db, stranger)
    db.commit()
    assert list(_desk(api, seller, clock)) == ["SIM-DISPUTE-A"]
    assert _desk(api, stranger, clock) == {}
    assert api.get("/api/seller/not-a-token/disputes/desk").status_code == 404
    buyer = LinkSigner(SECRET, clock).issue(order.id, scope="buyer")
    assert api.get(f"/api/seller/{buyer}/disputes/desk").status_code == 404


def test_DS_03_stage_follows_the_stored_reply(db, clock, api):
    seller = make_seller(db)
    _disputed(db, seller, dispute_id="D-NONE")
    o2, c2, d2 = _disputed(db, seller, dispute_id="D-EVID")
    o3, c3, d3 = _disputed(db, seller, dispute_id="D-OFFER")
    _o4, c4, _ = _disputed(db, seller, dispute_id="D-DONE", status="resolved")
    c4.state = CaseState.CLOSED.value
    for order, case, decision, kind, status in (
        (o2, c2, d2, "evidence", "completed"),
        (o3, c3, d3, "offer", "queued"),
    ):
        db.add(
            MoneyAction(
                order_id=order.id,
                case_id=case.id,
                decision_id=decision.id,
                seller_id=seller.id,
                kind=kind,
                amount_minor=0 if kind == "evidence" else order.amount_minor,
                currency=order.currency,
                request_id=f"req-{kind}",
                status=status,
            )
        )
    db.add(
        AuditLog(
            actor="paypal",
            action="transition: dispute resolved: RESOLVED_SELLER_FAVOUR",
            entity_type="case",
            entity_id=c4.id,
            before={},
            after={"state": "CLOSED"},
        )
    )
    db.commit()
    rows = _desk(api, seller, clock)
    assert {k: r["stage"] for k, r in rows.items()} == {
        "D-NONE": "needs_reply",
        "D-EVID": "evidence",
        "D-OFFER": "offer",
        "D-DONE": "resolved",
    }
    none = rows["D-NONE"]
    assert none["can_send_evidence"] and none["recommended"] == "evidence"
    assert none["offer_minor"] == 8900 and none["outcome"] is None
    assert not rows["D-EVID"]["can_send_evidence"] and rows["D-EVID"]["recommended"] is None
    assert rows["D-DONE"]["outcome"]["code"] == "RESOLVED_SELLER_FAVOUR"
    assert rows["D-DONE"]["outcome"]["result"] == "won"
    assert rows["D-DONE"]["timeline"][-1]["title"] == "Case closed"


def test_DS_04_paypal_facts_come_only_from_its_webhooks(db, clock, api):
    seller = make_seller(db)
    _disputed(db, seller, dispute_id="SIM-DISPUTE-B")
    _disputed(db, seller, dispute_id="PP-D-1", amount_minor=5000, currency="GBP", status="resolved")
    base = {"dispute_id": "PP-D-1", "disputed_transactions": [{"buyer": {"name": BUYER_NAME}}]}
    _hook(
        db,
        clock,
        "CUSTOMER.DISPUTE.CREATED",
        {
            **base,
            "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
            "status": "WAITING_FOR_SELLER_RESPONSE",
            "dispute_life_cycle_stage": "INQUIRY",
            "dispute_channel": "INTERNAL",
            "seller_response_due_date": "2026-11-20T10:00:00.000Z",
            "dispute_amount": {"currency_code": "GBP", "value": "45.00"},
            "messages": [{"posted_by": "BUYER", "content": "buyer@example.com wrote this"}],
        },
    )
    _hook(
        db,
        clock,
        "CUSTOMER.DISPUTE.RESOLVED",
        {
            **base,
            "status": "RESOLVED",
            "dispute_outcome": {"outcome_code": "RESOLVED_BUYER_FAVOUR"},
        },
        minutes=5,
    )
    db.commit()
    rows = _desk(api, seller, clock)
    demo = rows["SIM-DISPUTE-B"]["paypal"]
    assert demo["events"] == 0 and demo["response_due_at"] is None and demo["reason"] is None
    assert rows["SIM-DISPUTE-B"]["amount_from"] == "order"
    real = rows["PP-D-1"]
    facts = real["paypal"]
    assert facts["reason"] == "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"
    assert facts["status"] == "RESOLVED" and facts["stage"] == "INQUIRY"
    assert facts["response_due_at"].startswith("2026-11-20T10:00")
    assert real["amount_minor"] == 4500 and real["amount_from"] == "paypal"
    assert real["outcome"] == {
        "code": "RESOLVED_BUYER_FAVOUR",
        "result": "lost",
        "at": real["outcome"]["at"],
    }
    titles = [e["title"] for e in real["timeline"]]
    assert titles.count("Dispute opened") == 1 and "PayPal resolved it" in titles
    body = json.dumps(real)
    for secret in (BUYER_NAME, "buyer@example.com", "Flat 2", "N1 9GU", "PAYER-1"):
        assert secret not in body


def test_DS_05_seller_protection_checks_read_stored_rows(db, clock, api):
    seller = make_seller(db)
    order, case, decision = _disputed(db, seller, dispute_id="PP-D-SP")
    shipment = make_shipment(db, order, tracking_number="SHIPPO_DELIVERED", carrier="usps")
    db.add(TrackingEvent(shipment_id=shipment.id, status="DELIVERED", occurred_at=clock.now()))
    db.add(
        Job(
            kind=ADD_TRACKING, payload={}, status="done", dedupe_key=f"{ADD_TRACKING}:{shipment.id}"
        )
    )
    db.add(
        AuditLog(
            actor="paypal",
            action="paypal.seller_protection",
            entity_type="order",
            entity_id=order.id,
            before={},
            after={"status": "ELIGIBLE", "dispute_categories": ["ITEM_NOT_RECEIVED"]},
        )
    )
    db.add(
        MoneyAction(
            order_id=order.id,
            case_id=case.id,
            decision_id=decision.id,
            seller_id=seller.id,
            kind="evidence",
            amount_minor=0,
            currency="USD",
            request_id="req-sp",
            status="completed",
            completed_at=clock.now(),
        )
    )
    db.commit()
    p = _desk(api, seller, clock)["PP-D-SP"]["protection"]
    assert p["status"] == "ELIGIBLE" and p["categories"] == ["ITEM_NOT_RECEIVED"]
    assert {c["id"]: c["state"] for c in p["checks"]} == {
        "verdict": "pass",
        "address": "pass",
        "delivered": "pass",
        "tracking": "pass",
        "replied": "pass",
    }
    assert (p["met"], p["total"]) == (5, 5)

    shipment.delivery_address = {**shipment.delivery_address, "line1": "99 Other Road"}
    db.commit()
    p = _desk(api, seller, clock)["PP-D-SP"]["protection"]
    assert {c["id"]: c["state"] for c in p["checks"]}["address"] == "fail"
    assert "99 Other Road" not in json.dumps(p)


def test_DS_05_a_check_with_no_fact_is_missing_never_passed(db, clock, api):
    seller = make_seller(db)
    _disputed(db, seller, dispute_id="SIM-DISPUTE-C")
    db.commit()
    p = _desk(api, seller, clock)["SIM-DISPUTE-C"]["protection"]
    states = {c["id"]: c["state"] for c in p["checks"]}
    assert states == {
        "verdict": "missing",
        "address": "missing",
        "delivered": "missing",
        "tracking": "missing",
        "replied": "missing",
    }
    assert p["met"] == 0


def test_DS_06_evidence_sent_shows_what_when_and_its_request_id(db, clock, api):
    seller = make_seller(db)
    order, case, decision = _disputed(db, seller, dispute_id="SIM-DISPUTE-E")
    ma = MoneyAction(
        order_id=order.id,
        case_id=case.id,
        decision_id=decision.id,
        seller_id=seller.id,
        kind="evidence",
        amount_minor=0,
        currency="USD",
        request_id="doorstep-dispute-req-1",
        status="completed",
        paypal_status="SIMULATED_COMPLETED",
        created_at=clock.now(),
        completed_at=clock.now() + timedelta(seconds=3),
    )
    db.add(ma)
    db.flush()
    notes = "Doorstep evidence for order X.\nCarrier: usps; tracking number: SHIPPO_DELIVERED."
    db.add(
        Job(
            kind="paypal.dispute_evidence",
            payload={
                "money_action_id": ma.id,
                "evidence": {
                    "evidence_type": "PROOF_OF_FULFILLMENT",
                    "notes": notes,
                    "carrier": "USPS",
                    "tracking_number": "SHIPPO_DELIVERED",
                },
            },
            status="done",
            dedupe_key=f"evidence:{ma.id}",
        )
    )
    for action, after in (
        ("evidence.queued", {"request_id": ma.request_id, "notes": notes}),
        ("evidence.completed", {"status": "completed", "paypal_status": "SIMULATED_COMPLETED"}),
    ):
        db.add(
            AuditLog(
                actor="policy",
                action=action,
                entity_type="money_action",
                entity_id=ma.id,
                before={},
                after=after,
            )
        )
    db.commit()
    row = _desk(api, seller, clock)["SIM-DISPUTE-E"]
    ev = row["evidence"]
    assert ev["status"] == "simulated" and ev["request_id"] == "doorstep-dispute-req-1"
    assert ev["evidence_type"] == "PROOF_OF_FULFILLMENT" and ev["carrier"] == "USPS"
    assert ev["notes"] == notes.splitlines() and ev["sent_at"] is not None
    titles = [e["title"] for e in row["timeline"]]
    assert "Evidence queued" in titles and "Evidence simulated" in titles
    replied = {c["id"]: c for c in row["protection"]["checks"]}["replied"]
    assert replied["state"] == "pass" and replied["detail"] == "Evidence sent"
