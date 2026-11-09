"""AD-01..04: the carrier scans "Delivered" after the buyer reported the parcel missing.

From seller research (Nov 9, 2026): sellers refund a parcel as lost, it then turns up, and
nobody tells them; a carrier claim stays open for a parcel the carrier says it delivered.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.api.views import buyer_view, case_view
from app.cases.engine import FailurePoint
from app.cases.late_delivery import (
    NotDeliveredAfterReport,
    buyer_says_arrived,
    late_delivery,
    record_delivered_scan,
)
from app.cases.signals import EvidenceKind
from app.orders.enums import CaseState, RecoveryStatus
from app.orders.models import AuditLog, Evidence, Recovery, TrackingEvent
from app.policy.fingerprint import evidence_fingerprint
from app.shipping.events import handle_shippo_track_updated
from app.worker.models import WebhookInbox
from tests.factories import add_decision, make_case, make_order, make_seller, make_shipment
from tests.unit.test_dp_delivery_proof import signer, web  # noqa: F401 - fixtures

TRACKING = "SHIPPO_DELIVERED"


def _reported(db, clock, *, state=CaseState.SETTLED, reason="not_received"):
    order = make_order(db, make_seller(db))
    shipment = make_shipment(db, order, tracking_number=TRACKING, carrier="royal_mail")
    db.add(
        TrackingEvent(
            shipment_id=shipment.id,
            status="TRANSIT",
            occurred_at=clock.now() - timedelta(days=9),
            applied=True,
        )
    )
    case = make_case(
        db, order, state=state, opened_at=clock.now() - timedelta(days=2), reason=reason
    )
    return order, shipment, case


def _claim(db, clock, order, case, status=RecoveryStatus.PREPARED):
    decision = add_decision(db, case)
    claim = Recovery(
        case_id=case.id,
        order_id=order.id,
        seller_id=order.seller_id,
        decision_id=decision.id,
        carrier="royal_mail",
        carrier_id="royal_mail",
        failure_point=FailurePoint.CARRIER_LOST.value,
        claim_minor=6000,
        currency="GBP",
        status=status.value,
        deadline_at=clock.now() + timedelta(days=20),
    )
    db.add(claim)
    db.flush()
    return claim


def _scan(db, shipment, when, status="DELIVERED") -> TrackingEvent:
    event = TrackingEvent(shipment_id=shipment.id, status=status, occurred_at=when, applied=False)
    db.add(event)
    db.flush()
    return event


def _webhook(db, when: str) -> None:
    row = WebhookInbox(
        event_id=f"{TRACKING}-DELIVERED-{when}",
        source="shippo",
        event_type="track_updated",
        payload={
            "event": "track_updated",
            "data": {
                "tracking_number": TRACKING,
                "tracking_status": {"status": "DELIVERED", "status_date": when},
            },
        },
        status="pending",
        attempts=0,
    )
    db.add(row)
    db.flush()
    handle_shippo_track_updated(db, row)
    db.flush()


# ---------------------------------------------------------------- AD-01 recorded once


def test_AD_01_a_delivered_scan_after_the_report_is_recorded_once(db, clock):
    order, shipment, case = _reported(db, clock)
    first = _scan(db, shipment, clock.now() - timedelta(hours=3))
    assert record_delivered_scan(db, order, first, now=clock.now()) is True
    again = _scan(db, shipment, clock.now() - timedelta(hours=1))
    assert record_delivered_scan(db, order, again, now=clock.now()) is False
    late = late_delivery(db, order.id)
    assert late is not None and late.case_id == case.id
    assert late.delivered_at == first.occurred_at and late.buyer_says_arrived is False
    rows = db.scalars(
        select(AuditLog).where(AuditLog.action == "order.delivered_after_report")
    ).all()
    assert len(rows) == 1


def test_AD_01_a_delivered_scan_before_the_report_is_an_ordinary_case(db, clock):
    order, shipment, _ = _reported(db, clock, state=CaseState.AWAITING_SELLER)
    before = _scan(db, shipment, clock.now() - timedelta(days=3))
    assert record_delivered_scan(db, order, before, now=clock.now()) is False
    # A repeat of that delivered scan after the report is still the same ordinary case.
    later = _scan(db, shipment, clock.now() - timedelta(hours=1))
    assert record_delivered_scan(db, order, later, now=clock.now()) is False
    assert late_delivery(db, order.id) is None


@pytest.mark.parametrize("reason", ["damaged", "wrong_item"])
def test_AD_01_only_a_not_received_report_counts(db, clock, reason):
    order, shipment, _ = _reported(db, clock, reason=reason)
    event = _scan(db, shipment, clock.now() - timedelta(hours=1))
    assert record_delivered_scan(db, order, event, now=clock.now()) is False


def test_AD_01_no_case_or_another_status_changes_nothing(db, clock):
    order = make_order(db, make_seller(db))
    shipment = make_shipment(db, order, tracking_number=TRACKING)
    event = _scan(db, shipment, clock.now())
    assert record_delivered_scan(db, order, event, now=clock.now()) is False
    order2, shipment2, _ = _reported(db, clock)
    transit = _scan(db, shipment2, clock.now(), status="TRANSIT")
    assert record_delivered_scan(db, order2, transit, now=clock.now()) is False


def test_AD_01_the_shippo_webhook_records_it(db, clock):
    order, _, case = _reported(db, clock)
    _webhook(db, (clock.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    late = late_delivery(db, order.id)
    assert late is not None and late.case_id == case.id
    assert order.state == "RESOLVED"  # AD-04: the order's state is unchanged


# ---------------------------------------------------------------- AD-02 the carrier claim


def test_AD_02_an_unsubmitted_claim_is_withdrawn(db, clock):
    order, shipment, case = _reported(db, clock)
    claim = _claim(db, clock, order, case)
    record_delivered_scan(db, order, _scan(db, shipment, clock.now()), now=clock.now())
    assert claim.status == RecoveryStatus.WITHDRAWN.value
    assert late_delivery(db, order.id).claim_withdrawn is True  # type: ignore[union-attr]


@pytest.mark.parametrize("status", [RecoveryStatus.SUBMITTED, RecoveryStatus.PAID])
def test_AD_02_a_claim_the_seller_submitted_is_left_to_them(db, clock, status):
    order, shipment, case = _reported(db, clock)
    claim = _claim(db, clock, order, case, status=status)
    record_delivered_scan(db, order, _scan(db, shipment, clock.now()), now=clock.now())
    assert claim.status == status.value
    assert late_delivery(db, order.id).claim_withdrawn is False  # type: ignore[union-attr]


# ---------------------------------------------------------------- AD-03 the buyer's tap


def test_AD_03_the_buyer_says_it_arrived_once(db, clock):
    order, shipment, _ = _reported(db, clock, state=CaseState.AWAITING_SELLER)
    record_delivered_scan(db, order, _scan(db, shipment, clock.now()), now=clock.now())
    before = evidence_fingerprint(db, order.id)
    buyer_says_arrived(db, order, actor="buyer", now=clock.now())
    buyer_says_arrived(db, order, actor="buyer", now=clock.now())
    [proof] = db.scalars(
        select(Evidence).where(
            Evidence.order_id == order.id,
            Evidence.kind == EvidenceKind.BUYER_CONFIRMATION.value,
        )
    ).all()
    assert proof.kind == EvidenceKind.BUYER_CONFIRMATION.value
    # A refund still waiting is held for a person: the evidence it was decided on changed.
    assert evidence_fingerprint(db, order.id) != before
    assert late_delivery(db, order.id).buyer_says_arrived is True  # type: ignore[union-attr]


def test_AD_03_it_arrived_needs_a_delivered_scan_after_a_report(db, clock):
    order, _, _ = _reported(db, clock)
    with pytest.raises(NotDeliveredAfterReport):
        buyer_says_arrived(db, order, actor="buyer", now=clock.now())


# ---------------------------------------------------------------- views


def test_AD_views_show_the_late_delivery_to_seller_and_buyer(db, clock):
    order, shipment, case = _reported(db, clock)
    _claim(db, clock, order, case)
    assert buyer_view(db, order).delivered_after_report_at is None
    record_delivered_scan(db, order, _scan(db, shipment, clock.now()), now=clock.now())
    seller = case_view(db, case).delivered_after_report
    assert seller is not None and seller.claim_withdrawn and not seller.buyer_says_arrived
    buyer = buyer_view(db, order)
    assert buyer.delivered_after_report_at == clock.now() and buyer.arrival_confirmed is False
    buyer_says_arrived(db, order, actor="buyer", now=clock.now())
    assert buyer_view(db, order).arrival_confirmed is True


# ---------------------------------------------------------------- AD-03 over HTTP


def test_AD_03_the_buyer_route(db, clock, web):  # noqa: F811
    api, links = web
    order, shipment, _ = _reported(db, clock)
    db.commit()
    url = f"/api/buyer/{links.issue(order.id, scope='buyer')}/arrived"
    assert api.post(url).status_code == 409  # no delivered scan after the report yet
    record_delivered_scan(db, order, _scan(db, shipment, clock.now()), now=clock.now())
    db.commit()
    answer = api.post(url)
    assert answer.status_code == 200 and answer.json()["arrival_confirmed"] is True
    assert api.post("/api/buyer/not-a-token/arrived").status_code == 404
