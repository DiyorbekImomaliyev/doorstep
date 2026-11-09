"""AT-07: what the agent reads changes what happens next.

Found by the Nov 7 review: the agent's prompt fixed the order of its steps, every reason read
"standard case check", and no observation ever changed request_refund. Now each PayPal read
becomes a typed observation, and code reacts to it, at most once per run, through Doorstep's own
paths (never a PayPal write; the read-only allowlist stays as it is):

- PayPal lists an open dispute on this payment: it is recorded, so no refund goes out beside it,
  and the case moves to dispute evidence.
- PayPal has no tracking on the payment: the tracking outbox job is queued (or re-queued once if it
  failed, with the same PayPal-Request-Id).
- PayPal has not received a payment Doorstep records as received: the case is held for a person.

request_refund is asked only when PayPal agrees with Doorstep's record.
"""

# ruff: noqa: F811 - pytest fixtures imported from test_at_agent
from __future__ import annotations

import json

from sqlalchemy import select

from app.agent.runner import DoorstepAgent
from app.ai.llm import LLMAdapter
from app.ai.schemas import AgentStep
from app.api.views import timeline
from app.cases.reactions import (
    hold_unreceived_payment,
    queue_missing_tracking,
    record_listed_disputes,
)
from app.orders.enums import CaseState, MoneyActionStatus
from app.orders.models import Dispute, MoneyAction, Order, Shipment
from app.worker.handlers import ADD_TRACKING
from app.worker.models import Job
from app.worker.runner import DONE, FAILED, enqueue
from tests.factories import make_order, make_seller
from tests.unit.test_at_agent import FakeTools, Scripted, _case, pipeline  # noqa: F401

OPEN = [{"dispute_id": "PP-D-1", "status": "OPEN"}]


def _answer(tools: FakeTools, name: str, body: dict) -> FakeTools:
    tools[name] = tools._tool(name, body)
    return tools


def _pending_payment(tools: FakeTools) -> FakeTools:
    capture = {
        "id": "CAP-1",
        "status": "PENDING",
        "amount": {"value": "40.00", "currency_code": "GBP"},
    }
    return _answer(
        tools,
        "get_order_details",
        {
            "id": "PPO-1",
            "status": "COMPLETED",
            "purchase_units": [{"payments": {"captures": [capture]}}],
        },
    )


def _tools(run) -> list[str]:
    return [s.tool for s in run.steps]


def _tracking_jobs(db, order_id: str) -> list[Job]:
    shipment = db.scalars(select(Shipment).where(Shipment.order_id == order_id)).one()
    return [
        j
        for j in db.scalars(select(Job).where(Job.kind == ADD_TRACKING))
        if j.payload == {"shipment_id": shipment.id}
    ]


# ---------------------------------------------------------------- a dispute PayPal lists


def test_AT_07_a_listed_dispute_is_recorded_and_the_case_goes_to_evidence(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    assert case.state == CaseState.AWAITING_SELLER.value
    run = DoorstepAgent(FakeTools(disputes=OPEN)).run(db, case, clock=clock)
    tools = _tools(run)
    assert tools.index("record_dispute") == tools.index("list_disputes") + 1
    assert "get_dispute" in tools  # it reads the dispute it found
    assert "request_refund" not in tools  # PayPal decides a dispute; no refund beside it
    dispute = db.scalars(select(Dispute).where(Dispute.order_id == case.order_id)).one()
    assert (dispute.paypal_dispute_id, dispute.status) == ("PP-D-1", "open")
    assert case.state == CaseState.DISPUTE_EVIDENCE.value
    assert "dispute" in run.steps[-1].summary.lower()


def test_AT_07_a_dispute_beside_a_waiting_refund_is_left_to_the_refund_recheck(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=4000)  # under the limit: the refund is queued
    assert case.state == CaseState.AUTO_REFUND.value
    run = DoorstepAgent(FakeTools(disputes=OPEN)).run(db, case, clock=clock)
    step = next(s for s in run.steps if s.tool == "record_dispute")
    assert "checked" in step.summary  # the refund job checks open disputes before PayPal
    assert db.scalars(select(Dispute).where(Dispute.order_id == case.order_id)).one()
    assert case.state == CaseState.AUTO_REFUND.value  # the money path routes it, not the agent
    ma = db.scalars(select(MoneyAction).where(MoneyAction.case_id == case.id)).one()
    assert ma.status == MoneyActionStatus.QUEUED.value


def test_AT_07_a_resolved_dispute_changes_nothing(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=4000)
    resolved = [{"dispute_id": "PP-D-9", "status": "RESOLVED"}]
    run = DoorstepAgent(FakeTools(disputes=resolved)).run(db, case, clock=clock)
    assert "record_dispute" not in _tools(run)
    assert run.outcome == "refund_queued"
    assert db.scalars(select(Dispute)).all() == []


def test_AT_07_a_known_dispute_is_not_recorded_twice(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    DoorstepAgent(FakeTools(disputes=OPEN)).run(db, case, clock=clock)
    DoorstepAgent(FakeTools(disputes=OPEN)).run(db, case, clock=clock)
    assert len(db.scalars(select(Dispute)).all()) == 1


def test_AT_07_the_model_cannot_ask_for_a_refund_beside_a_dispute(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=4000)
    llm = LLMAdapter(
        Scripted(
            AgentStep(tool="list_disputes", reason="check disputes"),
            AgentStep(tool="list_disputes", reason="check again"),
            AgentStep(tool="request_refund", reason="refund anyway"),
            AgentStep(tool="finish", reason="done"),
        )
    )
    run = DoorstepAgent(FakeTools(disputes=OPEN), llm=llm).run(db, case, clock=clock)
    asked = next(s for s in run.steps if s.tool == "request_refund")
    assert asked.summary.startswith("Not asked")
    assert run.outcome is None
    assert _tools(run).count("record_dispute") == 1  # a reaction runs once per run


# ---------------------------------------------------------------- tracking PayPal lacks


def test_AT_07_missing_tracking_queues_the_tracking_job(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    tools = _answer(FakeTools(), "get_shipment_tracking", {"trackers": []})
    run = DoorstepAgent(tools).run(db, case, clock=clock)
    names = _tools(run)
    assert names.index("queue_tracking") == names.index("get_shipment_tracking") + 1
    [job] = _tracking_jobs(db, case.order_id)
    assert job.status == "queued"
    assert "request_refund" in names  # missing tracking alone does not stop the policy


def test_AT_07_a_queued_tracking_job_is_not_queued_twice(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    shipment = db.scalars(select(Shipment).where(Shipment.order_id == case.order_id)).one()
    enqueue(
        db,
        ADD_TRACKING,
        {"shipment_id": shipment.id},
        clock=clock,
        dedupe_key=f"{ADD_TRACKING}:{shipment.id}",
    )
    tools = _answer(FakeTools(), "get_shipment_tracking", {"trackers": []})
    run = DoorstepAgent(tools).run(db, case, clock=clock)
    assert len(_tracking_jobs(db, case.order_id)) == 1
    assert "already queued" in next(s for s in run.steps if s.tool == "queue_tracking").summary


def test_AT_07_a_failed_tracking_job_is_requeued_once(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    shipment = db.scalars(select(Shipment).where(Shipment.order_id == case.order_id)).one()
    failed = enqueue(
        db,
        ADD_TRACKING,
        {"shipment_id": shipment.id},
        clock=clock,
        dedupe_key=f"{ADD_TRACKING}:{shipment.id}",
    )
    failed.status = FAILED
    db.flush()
    for _ in range(2):  # a second run never queues a third job
        tools = _answer(FakeTools(), "get_shipment_tracking", {"trackers": []})
        DoorstepAgent(tools).run(db, case, clock=clock)
    jobs = _tracking_jobs(db, case.order_id)
    assert sorted(j.status for j in jobs) == ["failed", "queued"]
    # Same payload, so the handler sends the same PayPal-Request-Id: PayPal records it once.
    assert all(j.payload == {"shipment_id": shipment.id} for j in jobs)


def test_AT_07_tracking_on_paypal_needs_nothing(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    run = DoorstepAgent(FakeTools()).run(db, case, clock=clock)
    assert "queue_tracking" not in _tools(run)
    assert _tracking_jobs(db, case.order_id) == []


# ---------------------------------------------------------------- a payment PayPal lacks


def test_AT_07_a_payment_paypal_has_not_received_holds_the_case(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    run = DoorstepAgent(_pending_payment(FakeTools())).run(db, case, clock=clock)
    names = _tools(run)
    assert names.index("hold_for_review") == names.index("get_order_details") + 1
    assert case.state == CaseState.HOLD_FOR_REVIEW.value
    assert "request_refund" not in names
    assert "PENDING" in next(s for s in run.steps if s.tool == "hold_for_review").summary


def test_AT_07_a_payment_mismatch_beside_a_waiting_refund_is_left_to_the_recheck(
    db, pipeline, clock
):
    case = _case(db, pipeline, amount_minor=4000)
    run = DoorstepAgent(_pending_payment(FakeTools())).run(db, case, clock=clock)
    step = next(s for s in run.steps if s.tool == "hold_for_review")
    assert "checked" in step.summary
    assert case.state == CaseState.AUTO_REFUND.value


def test_AT_07_a_payment_doorstep_also_lacks_is_not_a_mismatch(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    order = db.get(Order, case.order_id)
    order.capture_status = "PENDING"
    db.flush()
    run = DoorstepAgent(_pending_payment(FakeTools())).run(db, case, clock=clock)
    assert "hold_for_review" not in _tools(run)  # the policy already holds an unpaid order
    assert "request_refund" not in _tools(run)
    assert "PENDING" in run.steps[-1].summary  # the finish says why no refund was asked


# ---------------------------------------------------------------- reasons and the trace


def test_AT_07_the_fixed_plan_says_why_each_step_runs(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=4000)
    run = DoorstepAgent(FakeTools()).run(db, case, clock=clock)
    reasons = [s.reason for s in run.steps if s.tool != "finish"]
    assert "standard case check" not in reasons
    assert len(set(reasons)) == len(reasons)


def test_AT_07_reactions_are_audited_and_named_in_the_seller_timeline(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    DoorstepAgent(FakeTools(disputes=OPEN)).run(db, case, clock=clock)
    order = db.get(Order, case.order_id)
    titles = [i.title for i in timeline(db, order, for_buyer=False) if i.kind == "agent"]
    assert "Agent: found an open PayPal dispute" in titles


def test_AT_07_the_planner_sees_what_doorstep_did(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    provider = Scripted(
        AgentStep(tool="list_disputes", reason="check disputes"),
        AgentStep(tool="finish", reason="done"),
    )
    DoorstepAgent(FakeTools(disputes=OPEN), llm=LLMAdapter(provider)).run(db, case, clock=clock)
    second = provider.calls[1]["user"]
    assert "record_dispute" in second
    assert json.dumps("PP-D-1")[1:-1] in second


def test_AT_07_a_dispute_already_on_file_is_named_as_such(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    DoorstepAgent(FakeTools(disputes=OPEN)).run(db, case, clock=clock)
    again = DoorstepAgent(FakeTools(disputes=OPEN)).run(db, case, clock=clock)
    step = next(s for s in again.steps if s.tool == "record_dispute")
    assert step.summary.startswith("Dispute PP-D-1 is already on file")
    assert "already sends evidence" in step.summary


def test_AT_07_a_held_case_keeps_its_person_when_a_dispute_appears(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    case.state = CaseState.HOLD_FOR_REVIEW.value  # a person owns it; it cannot jump to evidence
    db.flush()
    run = DoorstepAgent(FakeTools(disputes=OPEN)).run(db, case, clock=clock)
    step = next(s for s in run.steps if s.tool == "record_dispute")
    assert "a person sees the dispute" in step.summary
    assert case.state == CaseState.HOLD_FOR_REVIEW.value


# ---------------------------------------------------------------- reactions, one by one


def test_AT_07_a_dispute_paypal_reopened_is_open_again(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    db.add(
        Dispute(
            order_id=case.order_id,
            paypal_dispute_id="PP-D-1",
            status="resolved",
            opened_at=clock.now(),
        )
    )
    db.flush()
    text = record_listed_disputes(db, case, ["PP-D-1"], clock=clock)
    assert text.startswith("Recorded dispute PP-D-1")
    assert db.scalars(select(Dispute)).one().status == "open"


def test_AT_07_a_dispute_on_file_for_another_order_is_left_alone(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    other = make_order(db, make_seller(db))
    db.add(
        Dispute(
            order_id=other.id,
            paypal_dispute_id="PP-D-1",
            status="resolved",
            opened_at=clock.now(),
        )
    )
    db.flush()
    record_listed_disputes(db, case, ["PP-D-1"], clock=clock)
    row = db.scalars(select(Dispute)).one()
    assert (row.order_id, row.status) == (other.id, "resolved")


def test_AT_07_no_tracking_number_means_nothing_to_queue(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    shipment = db.scalars(select(Shipment).where(Shipment.order_id == case.order_id)).one()
    shipment.tracking_number = ""
    db.flush()
    order = db.get(Order, case.order_id)
    assert queue_missing_tracking(db, order, clock=clock).startswith("No tracking number yet")
    assert db.scalars(select(Job).where(Job.kind == ADD_TRACKING)).all() == []


def test_AT_07_a_tracking_job_that_failed_twice_is_left_to_a_person(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    shipment = db.scalars(select(Shipment).where(Shipment.order_id == case.order_id)).one()
    key = f"{ADD_TRACKING}:{shipment.id}"
    for dedupe in (key, f"{key}:retry"):
        job = enqueue(
            db, ADD_TRACKING, {"shipment_id": shipment.id}, clock=clock, dedupe_key=dedupe
        )
        job.status = FAILED
    db.flush()
    order = db.get(Order, case.order_id)
    assert "failed twice" in queue_missing_tracking(db, order, clock=clock)
    assert len(_tracking_jobs(db, case.order_id)) == 2


def test_AT_07_a_tracking_job_that_ran_is_not_sent_again(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    shipment = db.scalars(select(Shipment).where(Shipment.order_id == case.order_id)).one()
    key = f"{ADD_TRACKING}:{shipment.id}"
    job = enqueue(db, ADD_TRACKING, {"shipment_id": shipment.id}, clock=clock, dedupe_key=key)
    job.status = DONE
    db.flush()
    order = db.get(Order, case.order_id)
    assert "sent tracking" in queue_missing_tracking(db, order, clock=clock)
    assert len(_tracking_jobs(db, case.order_id)) == 1


def test_AT_07_an_answer_without_a_status_is_unknown_never_a_mismatch(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    capture = {"id": "CAP-1", "amount": {"value": "40.00", "currency_code": "GBP"}}  # no status
    body = {"id": "PPO-1", "purchase_units": [{"payments": {"captures": [capture]}}]}
    run = DoorstepAgent(_answer(FakeTools(), "get_order_details", body)).run(db, case, clock=clock)
    assert "hold_for_review" not in _tools(run)
    assert "request_refund" in _tools(run)  # unknown is not a reason to stop: the policy decides
    assert case.state == CaseState.AWAITING_SELLER.value


def test_AT_07_a_tracking_answer_of_another_shape_is_unknown(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    tools = _answer(FakeTools(), "get_shipment_tracking", {"id": "something else"})
    run = DoorstepAgent(tools).run(db, case, clock=clock)
    assert "queue_tracking" not in _tools(run)
    assert _tracking_jobs(db, case.order_id) == []


def test_AT_07_an_unpaid_payment_on_a_disputed_case_changes_nothing(db, pipeline, clock):
    case = _case(db, pipeline, amount_minor=60000)
    case.state = CaseState.DISPUTE_EVIDENCE.value  # PayPal is deciding: no hold on top
    db.flush()
    text = hold_unreceived_payment(db, case, "REVERSED")
    assert "nothing changes" in text
    assert case.state == CaseState.DISPUTE_EVIDENCE.value
