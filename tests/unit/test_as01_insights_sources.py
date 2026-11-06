"""AS-01: the Insights dashboard reads the seller's data as related tables, with no addresses."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.deps import AppDeps, Services
from app.main import create_app
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner


def _api(session_factory, settings, clock) -> TestClient:
    services = Services(
        links=LinkSigner("a" * 32, clock),
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


def test_AS_01_insights_tables_link_by_id(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    token = api.post("/demo/seed?scratch=1").json()["seller_token"]
    r = api.get(f"/api/seller/{token}/insights")
    assert r.status_code == 200, r.text
    data = r.json()
    orders = {o["order_id"] for o in data["orders"]}
    assert len(orders) == len(api.get(f"/api/seller/{token}/orders").json())
    assert data["shipments"] and data["cases"] and data["money_actions"]
    # Relationships: every row points at one of this seller's orders (and cases).
    cases = {c["case_id"] for c in data["cases"]}
    assert all(x["order_id"] in orders for x in data["shipments"])
    assert all(c["order_id"] in orders for c in data["cases"])
    assert all(m["order_id"] in orders for m in data["money_actions"])
    assert all(r["case_id"] in cases for r in data["recoveries"])
    assert any(c["failure_point"] for c in data["cases"])
    assert all(isinstance(o["amount_minor"], int) for o in data["orders"])


def test_AS_01_insights_send_no_address_or_buyer_words(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    token = api.post("/demo/seed?scratch=1").json()["seller_token"]
    data = api.get(f"/api/seller/{token}/insights").json()
    text = str(data).lower()
    for banned in ("address_line", "postal_code", "payer_id", "admin_area", "full_name", "email"):
        assert banned not in text
    assert set(data["orders"][0]) >= {"buyer_district", "buyer_country"}


def test_AS_01_insights_are_per_seller(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    mine = api.post("/demo/seed?scratch=1").json()["seller_token"]
    other = api.post("/demo/seed?scratch=1").json()["seller_token"]
    a = {o["order_id"] for o in api.get(f"/api/seller/{mine}/insights").json()["orders"]}
    b = {o["order_id"] for o in api.get(f"/api/seller/{other}/insights").json()["orders"]}
    assert a and b and not a & b
    assert api.get("/api/seller/not-a-token/insights").status_code in (401, 403, 404)
