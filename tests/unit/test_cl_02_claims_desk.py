"""CL-02 .. CL-07: the claims desk view, read only and seller scoped; its actions go through the
existing recovery mark endpoint, so the desk shows each mark with who made it."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import AppDeps, Services
from app.main import create_app
from app.orders.enums import CaseState
from app.orders.models import AuditLog, MoneyAction, Recovery
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner
from tests.factories import make_case, make_order, make_seller

SECRET = "claims-desk-test-secret-0123456789"
BUYER_NAME = "Buyer Name Secret"
PACK = {
    "source": "model",
    "facts": [{"id": "F1", "kind": "payment", "text": "The buyer paid £45 through PayPal."}],
    "sentences": [{"text": "The buyer paid £45 through PayPal.", "fact_ids": ["F1"]}],
}
TERMS = {
    "carrier_name": "Royal Mail",
    "is_default": False,
    "cover_minor": 7500,
    "item_minor": 4500,
    "window_days": 30,
    "window_starts": "posting",
    "service_basis": "Tracked 24 / Tracked 48, retail",
    "terms_url": "https://www.royalmail.com/retail-compensation-policy-damage",
    "note": "",
    "rule_id": "carrier_claims_v1",
    "rule_version": "2026-11-09",
}


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
    return LinkSigner(SECRET, clock).issue(seller.id, scope=scope)


def _shop(db, clock):
    """A ready claim (Royal Mail, refunded to the buyer) and a paid one (USD, default carrier)."""
    seller = make_seller(db)
    gbp = make_order(db, seller, amount_minor=4500, currency="GBP")
    gbp.item_description = "Denim jacket"
    gbp.shipping_address = {"name": BUYER_NAME, "line1": "1 Secret Road", "postal_code": "N1 9GU"}
    usd = make_order(db, seller, amount_minor=8900, currency="USD", buyer_country="US")
    usd.item_description = "Wireless headphones"
    usd.capture_id = "SIM-CAP-1"
    case = make_case(db, gbp, state=CaseState.SETTLED)
    case2 = make_case(db, usd, state=CaseState.SETTLED)
    ready = Recovery(
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
        deadline_at=clock.now() + timedelta(days=5),
        terms=TERMS,
        pack=PACK,
        created_at=clock.now() - timedelta(days=2),
    )
    paid = Recovery(
        case_id=case2.id,
        order_id=usd.id,
        seller_id=seller.id,
        decision_id="d2",
        carrier="shippo",
        carrier_id="default",
        failure_point="DAMAGED_IN_TRANSIT",
        claim_minor=8900,
        currency="USD",
        status="PAID",
        deadline_at=clock.now() + timedelta(days=9),
        terms={"carrier_name": "Your carrier", "is_default": True, "window_days": 14},
        pack={},
        recovered_minor=6000,
        created_at=clock.now() - timedelta(days=6),
        submitted_at=clock.now() - timedelta(days=4),
        resolved_at=clock.now() - timedelta(days=1),
    )
    db.add_all([ready, paid])
    db.flush()
    db.add_all(
        [
            MoneyAction(
                order_id=gbp.id,
                case_id=case.id,
                seller_id=seller.id,
                kind="refund",
                amount_minor=4500,
                currency="GBP",
                request_id="req-1",
                status="completed",
            ),
            MoneyAction(  # a failed refund never counts as money that went out
                order_id=gbp.id,
                case_id=case.id,
                seller_id=seller.id,
                kind="refund",
                amount_minor=4500,
                currency="GBP",
                request_id="req-2",
                status="failed",
            ),
            AuditLog(
                actor="pipeline",
                action="recovery.prepared",
                entity_type="recovery",
                entity_id=paid.id,
                before={},
                after={"status": "PREPARED", "claim_minor": 8900},
                at=clock.now() - timedelta(days=6),
            ),
            AuditLog(
                actor=f"seller:{seller.id}",
                action="recovery.submitted",
                entity_type="recovery",
                entity_id=paid.id,
                before={"status": "PREPARED"},
                after={"status": "SUBMITTED", "reason": "submitted", "recovered_minor": None},
                at=clock.now() - timedelta(days=4),
            ),
            AuditLog(
                actor="demo-seed",
                action="recovery.paid",
                entity_type="recovery",
                entity_id=paid.id,
                before={"status": "SUBMITTED"},
                after={"status": "PAID", "reason": "paid", "recovered_minor": 6000},
                at=clock.now() - timedelta(days=1),
            ),
        ]
    )
    db.commit()
    return seller, ready, paid


def _desk(api, seller, clock):
    res = api.get(f"/api/seller/{_token(seller, clock)}/claims/desk")
    assert res.status_code == 200
    return res.json()


def test_CL_02_desk_rows_carry_the_claim_story(db, clock, api):
    seller, ready, paid = _shop(db, clock)
    desk = _desk(api, seller, clock)
    assert desk["now"]
    rows = {r["recovery_id"]: r for r in desk["rows"]}
    assert list(rows) == [ready.id, paid.id]  # soonest deadline first
    r = rows[ready.id]
    assert r["item"] == "Denim jacket" and r["order_ref"] == r["order_id"][:8].upper()
    assert r["carrier_name"] == "Royal Mail" and r["carrier_known"] is True
    assert r["claim_minor"] == 4500 and r["cover_minor"] == 7500 and r["item_minor"] == 4500
    assert r["currency"] == "GBP" and r["window_days"] == 30 and r["window_starts"] == "posting"
    assert r["terms_url"].startswith("https://www.royalmail.com/")
    assert r["terms_rule"] == "carrier_claims_v1 2026-11-09"
    assert r["sentences"] == PACK["sentences"] and r["pack_source"] == "model"
    assert r["facts"][0]["id"] == "F1"
    assert r["actions"] == ["submitted", "withdraw"]
    assert r["refunded_minor"] == 4500  # the failed refund is left out
    assert r["simulated"] is False and r["case_state"] == "SETTLED"


def test_CL_04_history_names_who_moved_the_claim(db, clock, api):
    seller, _, paid = _shop(db, clock)
    row = next(r for r in _desk(api, seller, clock)["rows"] if r["recovery_id"] == paid.id)
    assert [(e["status"], e["by"]) for e in row["history"]] == [
        ("PREPARED", "doorstep"),
        ("SUBMITTED", "you"),
        ("PAID", "demo"),
    ]
    assert row["history"][2]["amount_minor"] == 6000
    assert all(e["note"] is None for e in row["history"])  # the seller's own marks need no note
    assert row["simulated"] is True and row["carrier_known"] is False
    assert row["actions"] == [] and row["recovered_minor"] == 6000


def test_CL_04_an_automatic_change_keeps_its_reason(db, clock, api):
    seller, ready, _ = _shop(db, clock)
    ready.status = "WITHDRAWN"
    db.add(
        AuditLog(
            actor="carrier",
            action="recovery.withdrawn",
            entity_type="recovery",
            entity_id=ready.id,
            before={"status": "PREPARED"},
            after={
                "status": "WITHDRAWN",
                "reason": "the carrier now scans the parcel as delivered",
            },
            at=clock.now(),
        )
    )
    db.commit()
    row = next(r for r in _desk(api, seller, clock)["rows"] if r["recovery_id"] == ready.id)
    assert row["history"][-1] == {
        "status": "WITHDRAWN",
        "at": row["history"][-1]["at"],
        "by": "carrier",
        "note": "the carrier now scans the parcel as delivered",
        "amount_minor": None,
    }


def test_CL_05_marks_go_through_the_existing_endpoint_and_show_on_the_desk(db, clock, api):
    seller, ready, _ = _shop(db, clock)
    t = _token(seller, clock)
    base = f"/api/seller/{t}/cases/{ready.case_id}/recovery"
    assert api.post(f"{base}/submitted").status_code == 200
    over_the_claim = api.post(f"{base}/paid", json={"amount_minor": 4501})
    assert over_the_claim.status_code == 422
    assert api.post(f"{base}/paid", json={"amount_minor": 4000}).status_code == 200
    assert api.post(f"{base}/withdraw").status_code == 409  # a paid claim is final
    row = next(r for r in _desk(api, seller, clock)["rows"] if r["recovery_id"] == ready.id)
    assert row["status"] == "PAID" and row["recovered_minor"] == 4000 and row["actions"] == []
    assert [(e["status"], e["by"], e["amount_minor"]) for e in row["history"]] == [
        ("SUBMITTED", "you", None),
        ("PAID", "you", 4000),
    ]


def test_CL_02_other_sellers_and_buyer_links_are_refused(db, clock, api):
    seller, _, _ = _shop(db, clock)
    stranger = make_seller(db)
    order = make_order(db, stranger)
    db.commit()
    assert _desk(api, stranger, clock)["rows"] == []
    assert api.get("/api/seller/not-a-token/claims/desk").status_code == 404
    buyer = LinkSigner(SECRET, clock).issue(order.id, scope="buyer")
    assert api.get(f"/api/seller/{buyer}/claims/desk").status_code == 404
    assert len(_desk(api, seller, clock)["rows"]) == 2


def test_CL_02_no_buyer_personal_data_in_the_desk(db, clock, api):
    seller, _, _ = _shop(db, clock)
    body = json.dumps(_desk(api, seller, clock))
    for secret in (BUYER_NAME, "Secret Road", "N1 9GU", "PAYER-1", "@", seller.id):
        assert secret not in body
