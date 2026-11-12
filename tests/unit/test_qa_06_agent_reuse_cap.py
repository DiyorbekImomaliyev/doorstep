"""QA-06: reopening a case reuses the agent's run (AT-06) without spending the model cap (SE-05).

Before, every drawer open counted against the 60-per-hour model cap even when the run was reused,
so a seller (or a judge) clicking through cases was locked out of the agent, the packing check and
the claim draft for the rest of the hour.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.deps import AppDeps, Services
from app.api.journey import AGENT_REUSE_S
from app.main import create_app
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner

MODEL_CAP_PER_CLIENT = 60


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


def _denim(api: TestClient) -> tuple[str, str]:
    token = api.post("/demo/seed?scratch=1").json()["seller_token"]
    rows = api.get(f"/api/seller/{token}/orders").json()
    denim = next(r for r in rows if r["item"].startswith("Denim"))
    return token, denim["case_id"]


def test_QA_06_reopening_a_case_never_spends_the_model_cap(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    token, case_id = _denim(api)
    url = f"/api/seller/{token}/agent/cases/{case_id}"
    first = api.post(url)
    assert first.status_code == 200
    for _ in range(MODEL_CAP_PER_CLIENT + 5):
        again = api.post(url)
        assert again.status_code == 200
        assert again.json() == first.json()


def test_QA_06_fresh_runs_still_count_against_the_cap(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    token, case_id = _denim(api)
    url = f"/api/seller/{token}/agent/cases/{case_id}"
    codes = []
    for _ in range(MODEL_CAP_PER_CLIENT + 1):
        codes.append(api.post(url).status_code)
        clock.advance(AGENT_REUSE_S + 1)  # past the reuse window: the agent runs again
    assert codes[:MODEL_CAP_PER_CLIENT] == [200] * MODEL_CAP_PER_CLIENT
    assert codes[-1] == 429
