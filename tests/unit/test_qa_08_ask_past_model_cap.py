"""QA-08: past the per-client model cap (SE-05), Ask Doorstep and Ask the grid still answer.

Before, the 61st question in an hour got a 429 and Insights showed an empty "Answered" step with
no figure. Now the free keyword reader answers past the cap, and the model is not called.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.ai.grid_agent import keyword_grid_plan
from app.ai.insights_agent import keyword_plan
from app.ai.schemas import GridMenu, InsightPlan
from app.api.deps import AppDeps, Services
from app.main import create_app
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner

MODEL_CAP_PER_CLIENT = 60
QUESTION = "Which carrier cost me most this month?"
MENU = GridMenu(status=["Lost by carrier", "On its way"], next=["Open"], method=["home"])


class CountingLLM:
    def __init__(self) -> None:
        self.calls = 0

    def complete_structured(self, *, schema: type[BaseModel], **_: Any) -> BaseModel:
        self.calls += 1
        if schema is InsightPlan:
            return InsightPlan(
                answerable=True, metric="net_loss", group_by="carrier", period="this_month"
            )
        raise AssertionError(f"unexpected schema {schema}")


def _api(session_factory, settings, clock, llm) -> TestClient:
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


def test_QA_08_past_the_cap_both_ask_routes_answer_by_keywords(session_factory, settings, clock):
    llm = CountingLLM()
    api = _api(session_factory, settings, clock, llm)
    token = api.post("/demo/seed?scratch=1").json()["seller_token"]
    ask = f"/api/seller/{token}/insights/ask"
    for _ in range(MODEL_CAP_PER_CLIENT):
        r = api.post(ask, json={"question": QUESTION})
        assert r.status_code == 200 and r.json()["mode"] == "model"
    calls_at_cap = llm.calls

    past = api.post(ask, json={"question": "  Which carrier   cost me most this month?  "})
    assert past.status_code == 200
    assert past.json() == {
        "question": QUESTION,
        "plan": keyword_plan(QUESTION).model_dump(),
        "mode": "keywords",
    }
    grid = api.post(
        f"/api/seller/{token}/grid/ask",
        json={"question": "lost parcels", "menu": MENU.model_dump()},
    )
    assert grid.status_code == 200
    assert grid.json()["mode"] == "keywords"
    assert grid.json()["plan"] == keyword_grid_plan("lost parcels", MENU).model_dump()
    assert llm.calls == calls_at_cap  # no model call past the cap


def test_QA_08_the_seller_is_still_checked_past_the_cap(session_factory, settings, clock):
    api = _api(session_factory, settings, clock, None)
    token = api.post("/demo/seed?scratch=1").json()["seller_token"]
    for _ in range(MODEL_CAP_PER_CLIENT + 1):
        api.post(f"/api/seller/{token}/insights/ask", json={"question": QUESTION})
    assert api.post("/api/seller/nope/insights/ask", json={"question": QUESTION}).status_code in (
        401,
        403,
        404,
    )
