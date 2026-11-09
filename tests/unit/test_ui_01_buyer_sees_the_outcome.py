"""UI-01: the buyer's page says what has happened since the decision, never the decision-time text.

Found by the Nov 7 review: after the seller approved, the buyer's "Resolved" page still said "the
seller has been asked to approve a refund". And PR-08: a simulated refund is never presented as a
real PayPal refund.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.api.views import buyer_view
from app.orders.enums import CaseState, MoneyActionKind, MoneyActionStatus
from app.orders.models import Decision, MoneyAction
from tests.factories import make_case, make_order, make_seller

DECISION_TEXT = "The seller has been asked to approve a refund of £60.00."


def _case(db, *, state=CaseState.AWAITING_SELLER, capture_id="auto"):
    seller = make_seller(db)
    order = make_order(db, seller, capture_id=capture_id)
    case = make_case(db, order, state=state)
    db.add(
        Decision(
            case_id=case.id,
            failure_point="CARRIER_LOST",
            confidence="high",
            route=state.value,
            rule_id="uk_possession_v1",
            rule_version="2026-11-04",
            model_outputs={"explainer": {"buyer_message": DECISION_TEXT}},
        )
    )
    db.flush()
    return order, case


def _refund(db, order, case, status, *, kind=MoneyActionKind.REFUND, external=False):
    db.add(
        MoneyAction(
            order_id=order.id,
            case_id=case.id,
            seller_id=order.seller_id,
            kind=kind.value,
            amount_minor=6000,
            currency="GBP",
            status=status.value,
            external=external,
            completed_at=datetime(2026, 11, 9, 10, 30, tzinfo=UTC)
            if status == MoneyActionStatus.COMPLETED
            else None,
        )
    )
    db.flush()


def test_UI_01_before_any_refund_the_decision_text_stands(db):
    order, _ = _case(db)
    assert buyer_view(db, order).case_message == DECISION_TEXT


def test_UI_01_an_approved_refund_on_its_way_replaces_the_request(db):
    order, case = _case(db)
    _refund(db, order, case, MoneyActionStatus.QUEUED)
    message = buyer_view(db, order).case_message
    assert message == "The seller approved your refund of £60.00. It is on its way to your PayPal."


def test_UI_01_a_completed_refund_says_when_it_was_sent(db):
    order, case = _case(db, state=CaseState.SETTLED)
    _refund(db, order, case, MoneyActionStatus.COMPLETED)
    message = buyer_view(db, order).case_message
    assert message == "Your refund of £60.00 was sent to your PayPal on 9 Nov."


def test_UI_01_PR_08_a_simulated_refund_says_so(db):
    order, case = _case(db, state=CaseState.SETTLED, capture_id="SIM-CAP-1")
    _refund(db, order, case, MoneyActionStatus.COMPLETED)
    message = buyer_view(db, order).case_message or ""
    assert message.startswith("Your refund of £60.00 was sent on 9 Nov.")
    assert "simulated" in message and "PayPal" not in message.split(".")[0]


def test_UI_01_a_held_refund_never_reads_as_refused(db):
    order, case = _case(db)
    _refund(db, order, case, MoneyActionStatus.BLOCKED)
    message = buyer_view(db, order).case_message or ""
    assert "person" in message and "Nothing has been refused" in message


def test_UI_01_an_external_refund_or_an_offer_does_not_replace_the_text(db):
    order, case = _case(db)
    _refund(db, order, case, MoneyActionStatus.COMPLETED, external=True)
    _refund(db, order, case, MoneyActionStatus.QUEUED, kind=MoneyActionKind.OFFER)
    assert buyer_view(db, order).case_message == DECISION_TEXT


def test_UI_01_after_approve_the_seller_is_offered_nothing_until_the_refund_lands(db):
    from app.api.views import case_view

    order, case = _case(db)
    assert case_view(db, case).actions == ["approve", "decline"]
    _refund(db, order, case, MoneyActionStatus.QUEUED)
    assert case_view(db, case).actions == []
