"""AD-05: a "Delivered" scan after the buyer's report holds a refund that has not gone out yet.

The scan is new evidence, so the pre-refund re-check (PO-13) holds the refund for a person
instead of sending it. Holding is not denying: under UK law a scan is not possession, so the
seller decides, with one tap to decide again. A refund PayPal already has is never touched.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.api.views import case_view
from app.cases.late_delivery import record_delivered_scan
from app.cases.signals import EvidenceKind
from app.orders.enums import CaseState, MoneyActionStatus
from app.orders.models import Evidence, MoneyAction, TrackingEvent
from tests.factories import make_shipment
from tests.helpers import engine_result, facts_for
from tests.unit import test_po_policy as _po

# The policy tests' fixtures (a captured order with a case, the policy, the refund worker).
policy, run_refunds, scenario, reload = _po.policy, _po.run_refunds, _po.scenario, _po.reload


def _late_scan(db, order, case) -> None:
    shipment = make_shipment(db, order, tracking_number="SHIPPO_DELIVERED", carrier="royal_mail")
    event = TrackingEvent(
        shipment_id=shipment.id,
        status="DELIVERED",
        occurred_at=case.opened_at + timedelta(minutes=5),
        applied=False,
    )
    db.add(event)
    db.flush()
    assert record_delivered_scan(db, order, event, now=event.occurred_at)


def test_AD_05_a_waiting_refund_is_held_for_a_person(
    db,
    policy,
    scenario,
    run_refunds,
    fake_paypal,
):
    _, order, case = scenario(3000)
    policy.decide(case, engine_result(), facts_for(order))
    [ma] = db.scalars(select(MoneyAction).where(MoneyAction.case_id == case.id)).all()
    assert ma.status == MoneyActionStatus.QUEUED.value
    _late_scan(db, order, case)
    run_refunds()
    assert fake_paypal.refund_calls == []
    db.refresh(ma)
    assert ma.status == MoneyActionStatus.BLOCKED.value
    assert ma.paypal_response["blocked_code"] == "evidence_changed"
    assert reload(db, case).state == CaseState.HOLD_FOR_REVIEW
    view = case_view(db, reload(db, case))
    assert view.delivered_after_report is not None and view.delivered_after_report.refund_held
    assert "re-check" in view.actions  # the seller can decide again in one tap


def test_AD_05_the_scan_is_recorded_as_evidence_once(db, scenario):
    _, order, case = scenario(3000)
    _late_scan(db, order, case)
    rows = db.scalars(
        select(Evidence).where(
            Evidence.order_id == order.id,
            Evidence.kind == EvidenceKind.CARRIER_DELIVERED_AFTER_REPORT.value,
        )
    ).all()
    assert len(rows) == 1 and rows[0].ai_result["case_id"] == case.id


def test_AD_05_a_refund_already_sent_is_not_touched(
    db,
    policy,
    scenario,
    run_refunds,
    fake_paypal,
):
    _, order, case = scenario(3000)
    policy.decide(case, engine_result(), facts_for(order))
    run_refunds()
    assert len(fake_paypal.refund_calls) == 1
    _late_scan(db, order, case)
    run_refunds()
    assert len(fake_paypal.refund_calls) == 1  # nothing new is sent, nothing is reversed
    view = case_view(db, reload(db, case))
    assert view.delivered_after_report is not None
    assert view.delivered_after_report.refund_held is False
