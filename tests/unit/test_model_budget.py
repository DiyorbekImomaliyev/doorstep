"""RC-08 and AT-07 — model budget: resetting the demo and opening cases cost no model calls.

Each judge reset seeds a whole shop; drafting AI claim packs for every seeded case and planning
every automatic agent check with the model spent the paid budget on text nobody asked for. The
fact-only claim pack and the fixed agent plan are what both fall back to anyway (true by
construction), so nothing a seller sees becomes less correct.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent.runner import FIXED_PLANNER
from app.api.deps import AppDeps, Services
from app.cases.recovery import DRAFT_RECOVERY
from app.main import create_app
from app.orders.models import Recovery
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner
from app.worker.models import Job

NOT_HERE = {"reason": "not_received", "note": ""}


class CountingModel:
    """Any call is a spend; it answers nothing useful, so a caller would fall back."""

    last_provider = "counting"

    def __init__(self):
        self.calls = 0

    def complete_structured(self, **_):
        self.calls += 1
        raise RuntimeError("no model in this test")


def _api(session_factory, settings, clock, llm=None) -> TestClient:
    services = Services(
        links=LinkSigner("a" * 32, clock),
        receipts=ReceiptSigner.generate(),
        orders=None,
        approved=None,
        agent_llm=llm,
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


def _drafts(session_factory) -> int:
    with session_factory() as s:
        return s.scalar(select(func.count()).select_from(Job).where(Job.kind == DRAFT_RECOVERY))


def test_RC_08_the_demo_seed_queues_no_claim_drafts(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    seed = api.post("/demo/seed?scratch=1").json()
    with session_factory() as s:
        packs = [r.pack["source"] for r in s.scalars(select(Recovery))]
    assert packs and set(packs) == {"template"}  # seeded cases still have a full claim pack
    assert _drafts(session_factory) == 0

    sam = api.post(f"/api/buyer/{seed['buyers']['sam-jacket']}/not-here", json=NOT_HERE)
    assert sam.status_code == 200, sam.text
    assert _drafts(session_factory) == 1  # a case reported during the demo gets the AI draft


def test_AT_07_the_automatic_pre_check_never_calls_the_model(session_factory, settings, clock):
    model = CountingModel()
    api = _api(session_factory, settings, clock, llm=model)
    seed = api.post("/demo/seed?scratch=1").json()
    token = seed["seller_token"]
    case_id = api.post(f"/api/buyer/{seed['buyers']['sam-jacket']}/not-here", json=NOT_HERE).json()[
        "case_id"
    ]

    auto = api.post(f"/api/seller/{token}/agent/cases/{case_id}?auto=1")
    assert auto.status_code == 200, auto.text
    assert auto.json()["planner"] == FIXED_PLANNER and model.calls == 0
    again = api.post(f"/api/seller/{token}/agent/cases/{case_id}")  # the button: reused
    assert again.json() == auto.json() and model.calls == 0


def test_AT_07_the_button_without_a_recent_run_may_plan_with_the_model(
    session_factory, settings, clock
):
    model = CountingModel()
    api = _api(session_factory, settings, clock, llm=model)
    seed = api.post("/demo/seed?scratch=1").json()
    case_id = api.post(f"/api/buyer/{seed['buyers']['sam-jacket']}/not-here", json=NOT_HERE).json()[
        "case_id"
    ]
    run = api.post(f"/api/seller/{seed['seller_token']}/agent/cases/{case_id}").json()
    assert model.calls >= 1  # asked the planner; it failed, so the fixed plan finished
    assert run["planner"] == FIXED_PLANNER and run["outcome"]
