"""AT over HTTP: the seller runs the agent on a case; seeded orders use simulated reads."""

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


def test_AT_04_seller_runs_the_agent_on_a_held_case(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    seed = api.post("/demo/seed?scratch=1").json()
    token = seed["seller_token"]
    rows = api.get(f"/api/seller/{token}/orders").json()
    denim = next(r for r in rows if r["item"].startswith("Denim"))
    r = api.post(f"/api/seller/{token}/agent/cases/{denim['case_id']}")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["planner"] == "fixed plan"
    assert run["outcome"] in {"held_for_review", "escalated_to_seller"}
    assert "simulated" in run["steps"][0]["summary"]
    page = api.get(f"/api/seller/{token}/cases/{denim['case_id']}").json()
    assert page["money"] == []
    assert any(i["kind"] == "agent" for i in page["timeline"])


def test_AT_01_another_seller_cannot_run_the_agent_on_my_case(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    mine = api.post("/demo/seed?scratch=1").json()
    rows = api.get(f"/api/seller/{mine['seller_token']}/orders").json()
    case_id = next(r["case_id"] for r in rows if r["case_id"])
    other = api.post("/demo/seed?scratch=1").json()["seller_token"]
    assert api.post(f"/api/seller/{other}/agent/cases/{case_id}").status_code == 404
