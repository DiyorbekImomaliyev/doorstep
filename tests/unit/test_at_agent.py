"""AT-01..05: the Doorstep agent. PayPal reads come from the Agent Toolkit with an explicit
allowlist; the only money tool is request_refund (the policy gate); every step is recorded and
shown in the case timeline. Without a model, a fixed plan runs the same steps."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.agent.runner import AGENT_TOOLS, DoorstepAgent
from app.agent.toolkit import ALLOWED_ACTIONS, PAYPAL_READ_TOOLS, paypal_tools
from app.ai.explainer import Explainer
from app.ai.llm import InvalidModelOutput, LLMAdapter
from app.ai.schemas import AgentStep
from app.api.views import timeline
from app.cases.engine import ClaimReason
from app.cases.pipeline import CasePipeline
from app.cases.signals import EvidenceKind
from app.orders.enums import CaseState, OrderState
from app.orders.models import AuditLog, Case, Evidence, MoneyAction, Order, TrackingEvent
from app.proof.receipts import ReceiptSigner
from tests.factories import make_order, make_seller, make_shipment


class FakeTools(dict):
    """PayPal read tools that answer like the toolkit (JSON text) and record their calls."""

    def __init__(self, disputes: list[dict] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        answers = {
            "get_order_details": {
                "id": "PPO-1",
                "status": "COMPLETED",
                "payer": {"email_address": "sam@example.com", "name": {"given_name": "Sam"}},
                "purchase_units": [
                    {
                        "payments": {
                            "captures": [
                                {
                                    "id": "CAP-1",
                                    "status": "COMPLETED",
                                    "amount": {"value": "40.00", "currency_code": "GBP"},
                                }
                            ]
                        },
                        "shipping": {"address": {"address_line_1": "1 Secret Street"}},
                    }
                ],
            },
            "get_shipment_tracking": {
                "trackers": [
                    {"tracking_number": "SHIPPO_DELIVERED", "carrier": "OTHER", "status": "SHIPPED"}
                ]
            },
            "list_disputes": {"items": disputes or []},
            "get_dispute": {
                "dispute_id": "PP-D-1",
                "status": "OPEN",
                "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
            },
        }
        for name, body in answers.items():
            self[name] = self._tool(name, body)

    def _tool(self, name, body):
        def call(args):
            self.calls.append((name, args))
            return json.dumps(body)

        return call


class Scripted:
    name = "scripted"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def generate(self, schema, **kw):
        self.calls.append(kw)
        answer = self.answers.pop(0) if self.answers else AgentStep(tool="finish", reason="done")
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def pipeline(db, rulebook, clock, settings) -> CasePipeline:
    return CasePipeline(db, rulebook, clock, settings, Explainer(), ReceiptSigner.generate())


def _case(db, pipeline, *, amount_minor: int) -> Case:
    order = make_order(db, make_seller(db), state=OrderState.DELIVERED, amount_minor=amount_minor)
    order.address_check = "valid"
    shipment = make_shipment(db, order, tracking_number="SHIPPO_DELIVERED")
    db.add(
        TrackingEvent(
            shipment_id=shipment.id, status="DELIVERED", occurred_at=order.created_at, applied=True
        )
    )
    db.add(
        Evidence(
            order_id=order.id,
            kind=EvidenceKind.PACKING_PHOTO,
            ai_result={"verdict": "match", "expected_weight_g": 900},
        )
    )
    db.flush()
    return db.get(
        Case, pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer").case_id
    )


# ---------------------------------------------------------------- AT-01 allowlist


def test_AT_01_toolkit_allowlist_is_read_only_and_explicit():
    assert ALLOWED_ACTIONS == {
        "orders": {"get": True},
        "shipment": {"get": True},
        "disputes": {"list": True, "get": True},
    }


def test_AT_01_the_real_toolkit_builds_exactly_the_allowed_tools():
    tools = paypal_tools("sandbox-client", "sandbox-secret")
    assert (
        set(tools)
        == PAYPAL_READ_TOOLS
        == {"get_order_details", "get_shipment_tracking", "list_disputes", "get_dispute"}
    )


def test_AT_02_the_agent_has_no_paypal_money_tool():
    assert set(AGENT_TOOLS) == PAYPAL_READ_TOOLS | {"request_refund", "finish"}
    risky = ("capture", "pay_order", "accept", "create", "update", "refund_order", "cancel")
    assert not any(word in name for name in AGENT_TOOLS for word in risky)


# ---------------------------------------------------------------- AT-03 / AT-04 runs


def test_AT_03_under_the_limit_the_agent_confirms_the_queued_refund(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=4000)
    run = DoorstepAgent(FakeTools()).run(db, case, clock=clock)
    tools = [s.tool for s in run.steps]
    assert tools[:3] == ["get_order_details", "get_shipment_tracking", "list_disputes"]
    assert "request_refund" in tools
    assert run.outcome == "refund_queued"
    assert db.scalars(select(MoneyAction).where(MoneyAction.case_id == case.id)).one()


def test_AT_04_over_the_limit_the_agent_escalates_and_no_refund_exists(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    run = DoorstepAgent(FakeTools()).run(db, case, clock=clock)
    assert run.outcome == "escalated_to_seller"
    assert run.steps[-1].tool == "finish"
    assert db.scalars(select(MoneyAction).where(MoneyAction.case_id == case.id)).all() == []
    assert case.state == CaseState.AWAITING_SELLER


def test_AT_02_the_agent_fills_ids_from_the_order_never_from_the_model(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=4000)
    order = db.get(Order, case.order_id)
    tools = FakeTools()
    DoorstepAgent(tools).run(db, case, clock=clock)
    calls = dict(tools.calls)
    assert calls["get_order_details"] == {"order_id": order.paypal_order_id}
    assert calls["list_disputes"] == {"disputed_transaction_id": order.capture_id}


def test_AT_02_get_dispute_only_for_a_dispute_paypal_listed(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    tools = FakeTools(disputes=[{"dispute_id": "PP-D-1", "status": "OPEN"}])
    llm = LLMAdapter(
        Scripted(
            AgentStep(tool="list_disputes", reason="check disputes"),
            AgentStep(tool="get_dispute", reason="read it", dispute_id="PP-D-OTHER"),
            AgentStep(tool="get_dispute", reason="read it", dispute_id="PP-D-1"),
            AgentStep(tool="finish", reason="done"),
        )
    )
    run = DoorstepAgent(tools, llm=llm).run(db, case, clock=clock)
    asked = [args for name, args in tools.calls if name == "get_dispute"]
    assert asked == [{"dispute_id": "PP-D-1"}]
    assert any("not listed" in s.summary for s in run.steps)


def test_AT_02_model_plan_is_bounded(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    loop = [AgentStep(tool="get_order_details", reason="again") for _ in range(20)]
    run = DoorstepAgent(FakeTools(), llm=LLMAdapter(Scripted(*loop)), max_steps=5).run(
        db, case, clock=clock
    )
    assert len(run.steps) <= 6  # max_steps, plus the closing finish
    assert run.steps[-1].tool == "finish"


def test_AT_02_invalid_planner_output_falls_back_to_the_fixed_plan(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=4000)
    bad = InvalidModelOutput("not json")
    run = DoorstepAgent(FakeTools(), llm=LLMAdapter(Scripted(bad, bad))).run(db, case, clock=clock)
    assert run.planner == "fixed plan"
    assert run.outcome == "refund_queued"


def test_AT_02_planner_sees_summaries_not_personal_data(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=4000)
    provider = Scripted(
        AgentStep(tool="get_order_details", reason="read the order"),
        AgentStep(tool="finish", reason="done"),
    )
    DoorstepAgent(FakeTools(), llm=LLMAdapter(provider)).run(db, case, clock=clock)
    second = provider.calls[1]["user"]
    assert "COMPLETED" in second
    assert "sam@example.com" not in second and "Secret Street" not in second


# ---------------------------------------------------------------- AT-05 traced


def test_AT_05_every_tool_call_is_audited_and_in_the_seller_timeline(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    run = DoorstepAgent(FakeTools()).run(db, case, clock=clock)
    rows = db.scalars(
        select(AuditLog).where(AuditLog.entity_id == case.id, AuditLog.actor == "doorstep-agent")
    ).all()
    assert [r.after["agent"] for r in rows] == [s.tool for s in run.steps]
    order = db.get(Order, case.order_id)
    seller_items = [i for i in timeline(db, order, for_buyer=False) if i.kind == "agent"]
    assert len(seller_items) == len(run.steps)
    assert all(i.title.startswith("Agent") for i in seller_items)
    assert not [i for i in timeline(db, order, for_buyer=True) if i.kind == "agent"]


def test_AT_05_audit_rows_carry_no_personal_data(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=4000)
    DoorstepAgent(FakeTools()).run(db, case, clock=clock)
    rows = db.scalars(select(AuditLog).where(AuditLog.actor == "doorstep-agent")).all()
    text = json.dumps([r.after for r in rows])
    assert "sam@example.com" not in text and "Secret Street" not in text


def test_AT_01_seeded_orders_use_simulated_reads_labelled_as_such(db, rulebook, clock, settings):
    from app.agent.runner import simulated_tools
    from app.demo.seed import DEMO_TAG, _id, seed_scenario

    seed_scenario(
        db, rulebook=rulebook, clock=clock, settings=settings, signer=ReceiptSigner.generate()
    )
    order = db.get(Order, _id(DEMO_TAG, "lost-denim"))
    tools = simulated_tools(db, order)
    body = json.loads(tools["get_order_details"]({"order_id": order.paypal_order_id}))
    assert body["simulated"] is True
