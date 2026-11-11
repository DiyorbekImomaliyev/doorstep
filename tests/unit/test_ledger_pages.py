"""NV-01, MN-01, DS-01: the claims, money and disputes lists a seller sees in the sidebar."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import AppDeps, Services
from app.main import create_app
from app.orders.enums import CaseState
from app.orders.models import AuditLog, Dispute, MoneyAction, Recovery
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner
from tests.factories import make_case, make_order, make_seller

SECRET = "ledger-test-secret-0123456789abcd"
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


def _shop(db, clock):
    seller = make_seller(db)
    gbp = make_order(db, seller, amount_minor=4500, currency="GBP")
    gbp.item_description = "Denim jacket"
    gbp.shipping_address = {"name": BUYER_NAME, "line1": "1 Secret Road", "postal_code": "N1 9GU"}
    usd = make_order(db, seller, amount_minor=8900, currency="USD", buyer_country="US")
    usd.item_description = "Wireless headphones"
    case = make_case(db, gbp)
    case2 = make_case(db, usd, state=CaseState.DISPUTE_EVIDENCE)
    db.add_all(
        [
            MoneyAction(
                order_id=gbp.id,
                case_id=case.id,
                seller_id=seller.id,
                kind="refund",
                amount_minor=4500,
                currency="GBP",
                request_id="req-real-1",
                status="completed",
                paypal_status="COMPLETED",
                paypal_refund_id="5RF1234",
            ),
            MoneyAction(
                order_id=usd.id,
                case_id=case2.id,
                seller_id=seller.id,
                kind="refund",
                amount_minor=8900,
                currency="USD",
                request_id="req-sim-1",
                status="completed",
                paypal_status="SIMULATED_COMPLETED",
                paypal_refund_id="SIM-REF-1",
            ),
            MoneyAction(
                order_id=usd.id,
                case_id=case2.id,
                seller_id=seller.id,
                kind="evidence",
                amount_minor=0,
                currency="USD",
                request_id="req-ev-1",
                status="completed",
            ),
            Recovery(
                case_id=case.id,
                order_id=gbp.id,
                seller_id=seller.id,
                decision_id="d1",
                carrier="royal_mail",
                carrier_id="royal_mail",
                failure_point="CARRIER_LOST",
                claim_minor=4500,
                currency="GBP",
                status="PREPARED",
                deadline_at=clock.now() + timedelta(days=12),
                terms={"carrier_name": "Royal Mail", "cover_minor": 5000, "window_days": 30},
            ),
            Dispute(
                order_id=usd.id,
                paypal_dispute_id="SIM-DISPUTE-1",
                status="open",
                opened_at=clock.now(),
            ),
            Dispute(order_id=gbp.id, paypal_dispute_id="PP-D-2", status="resolved"),
            AuditLog(
                actor="paypal",
                action="transition: dispute resolved: RESOLVED_SELLER_FAVOUR",
                entity_type="case",
                entity_id=case.id,
                before={},
                after={},
            ),
        ]
    )
    db.commit()
    return seller


def _token(seller, clock, scope="seller"):
    return LinkSigner(SECRET, clock).issue(seller.id, scope=scope)


def test_NV_01_claims_rows_are_seller_scoped(db, clock, api):
    seller = _shop(db, clock)
    rows = api.get(f"/api/seller/{_token(seller, clock)}/claims").json()
    assert len(rows) == 1
    r = rows[0]
    assert r["item"] == "Denim jacket" and r["status"] == "PREPARED"
    assert r["claim_minor"] == 4500 and r["cover_minor"] == 5000 and r["currency"] == "GBP"
    assert r["carrier_name"] == "Royal Mail"


def test_MN_01_money_rows_name_simulated_and_keep_currencies(db, clock, api):
    seller = _shop(db, clock)
    rows = api.get(f"/api/seller/{_token(seller, clock)}/money").json()
    by_key = {m["request_id"]: m for m in rows}
    assert by_key["req-sim-1"]["status"] == "simulated"
    assert by_key["req-real-1"]["status"] == "completed"
    assert by_key["req-real-1"]["paypal_refund_id"] == "5RF1234"
    assert {(m["currency"], m["amount_minor"]) for m in rows if m["kind"] == "refund"} == {
        ("GBP", 4500),
        ("USD", 8900),
    }


def test_DS_01_disputes_route_and_outcome(db, clock, api):
    seller = _shop(db, clock)
    rows = {
        d["paypal_dispute_id"]: d
        for d in api.get(f"/api/seller/{_token(seller, clock)}/disputes").json()
    }
    open_one = rows["SIM-DISPUTE-1"]
    assert open_one["status"] == "open" and open_one["route"] == "evidence"
    assert open_one["simulated"] and open_one["currency"] == "USD"
    done = rows["PP-D-2"]
    assert done["status"] == "resolved" and done["outcome"] == "RESOLVED_SELLER_FAVOUR"


@pytest.mark.parametrize("page", ["claims", "money", "disputes"])
def test_NV_01_other_sellers_and_buyer_links_are_refused(db, clock, api, page):
    seller = _shop(db, clock)
    stranger = make_seller(db)
    order = make_order(db, stranger)
    db.commit()
    assert api.get(f"/api/seller/{_token(stranger, clock)}/{page}").json() == []
    assert api.get(f"/api/seller/not-a-token/{page}").status_code == 404
    buyer = LinkSigner(SECRET, clock).issue(order.id, scope="buyer")
    assert api.get(f"/api/seller/{buyer}/{page}").status_code == 404
    assert api.get(f"/api/seller/{_token(seller, clock)}/{page}").status_code == 200


@pytest.mark.parametrize("page", ["claims", "money", "disputes"])
def test_MN_01_no_buyer_personal_data_in_any_response(db, clock, api, page):
    seller = _shop(db, clock)
    body = json.dumps(api.get(f"/api/seller/{_token(seller, clock)}/{page}").json())
    for secret in (BUYER_NAME, "Secret Road", "N1 9GU", "PAYER-1", "@"):
        assert secret not in body
