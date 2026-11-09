"""AT-07: how Doorstep reacts when the case agent's PayPal reads disagree with its records.

The agent only reads PayPal. When a read shows something Doorstep must act on, code here acts
through Doorstep's own paths and returns a sentence for the agent's trace:

- a dispute PayPal lists on this payment is recorded, so no refund goes out beside it;
- tracking PayPal lacks is sent again through the tracking outbox job;
- a payment PayPal has not received holds the case for a person.

Nothing here calls PayPal or moves money: the agent imports this module, and the import contracts
keep it away from every PayPal write. Case states change only through app/orders/state.py. A
refund already waiting is never touched here; the refund job re-checks open disputes and PayPal's
capture right before it sends anything.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.orders.enums import CaseState
from app.orders.models import Case, Dispute, Order, Shipment
from app.orders.state import CASE_TRANSITIONS, transition_case
from app.policy.inflight import refund_in_flight
from app.worker.kinds import ADD_TRACKING
from app.worker.models import Job
from app.worker.runner import DONE, FAILED, enqueue

AGENT = "doorstep-agent"
_RETRY = "retry"  # suffix of the one re-queue the agent may add after a failed tracking job


def _move(session: Session, case: Case, to: CaseState, reason: str) -> bool:
    """Move the case if that transition is allowed; True when it moved."""
    current = CaseState(case.state)
    if current == to or to not in CASE_TRANSITIONS[current]:
        return False
    transition_case(session, case, to, actor=AGENT, reason=reason)
    return True


def _state(case: Case) -> str:
    return case.state.lower().replace("_", " ")


def record_listed_disputes(
    session: Session, case: Case, dispute_ids: Iterable[str], *, clock: Clock
) -> str:
    """Disputes PayPal lists as open on this case's payment, which our table may not know yet
    (a late webhook). Recorded open, so the policy and the refund job see them."""
    ids = sorted(set(dispute_ids))
    new: list[str] = []
    for dispute_id in ids:
        row = session.scalar(select(Dispute).where(Dispute.paypal_dispute_id == dispute_id))
        if row is None or row.status != "open":
            new.append(dispute_id)
        if row is None:
            session.add(
                Dispute(
                    order_id=case.order_id,
                    paypal_dispute_id=dispute_id,
                    status="open",
                    opened_at=clock.now(),
                )
            )
        elif row.status != "open" and row.order_id == case.order_id:
            row.status = "open"  # PayPal reopened it
    session.flush()
    named = ", ".join(ids)
    found = f"Recorded dispute {named}." if new else f"Dispute {named} is already on file."
    if refund_in_flight(session, case.id):
        return (
            f"{found} The refund waiting in the outbox is checked against open disputes before "
            "PayPal, so it is not sent beside the dispute."
        )
    if case.state == CaseState.DISPUTE_EVIDENCE.value:
        return f"{found} The case already sends evidence through it; PayPal decides."
    if _move(session, case, CaseState.DISPUTE_EVIDENCE, "PayPal lists a dispute"):
        return f"{found} The case now sends evidence through it; PayPal decides."
    return f"{found} The case is {_state(case)}; a person sees the dispute before any refund."


def queue_missing_tracking(session: Session, order: Order, *, clock: Clock) -> str:
    """PayPal shows no tracking on the payment: send it through the tracking outbox job again.

    The job's PayPal-Request-Id comes from the shipment, so a re-queued job can never record the
    tracking twice at PayPal. A failed job is re-queued once; after that a person looks.
    """
    shipment = session.scalars(
        select(Shipment).where(Shipment.order_id == order.id).order_by(Shipment.created_at.desc())
    ).first()
    if shipment is None or not shipment.tracking_number:
        return "No tracking number yet, so there is nothing to add to the PayPal payment."
    number = shipment.tracking_number
    key = f"{ADD_TRACKING}:{shipment.id}"
    first = session.scalar(select(Job).where(Job.dedupe_key == key))
    retry = session.scalar(select(Job).where(Job.dedupe_key == f"{key}:{_RETRY}"))
    latest = retry or first
    if latest is not None and latest.status == FAILED and retry is not None:
        return f"Sending tracking {number} to PayPal failed twice; the Control Room shows it."
    if latest is not None and latest.status == DONE:
        return f"Doorstep sent tracking {number} to PayPal earlier; PayPal's read can lag behind."
    if latest is not None and latest.status != FAILED:
        return f"The job adding tracking {number} to PayPal is already queued."
    enqueue(
        session,
        ADD_TRACKING,
        {"shipment_id": shipment.id},
        clock=clock,
        dedupe_key=key if first is None else f"{key}:{_RETRY}",
    )
    again = "Re-queued" if first is not None else "Queued"
    return (
        f"{again} the job adding tracking {number} to the PayPal payment, so the buyer sees it. "
        "It runs through the outbox with one PayPal-Request-Id."
    )


def hold_unreceived_payment(session: Session, case: Case, paypal_status: str) -> str:
    """PayPal has not received a payment Doorstep records as received: a person checks."""
    if refund_in_flight(session, case.id):
        return (
            f"PayPal shows the payment as {paypal_status}. The refund waiting in the outbox is "
            "checked against PayPal's capture before it is sent."
        )
    reason = f"PayPal shows the payment as {paypal_status}"[:50]
    if _move(session, case, CaseState.HOLD_FOR_REVIEW, reason):
        return (
            f"PayPal shows the payment as {paypal_status}, but Doorstep has it as received. Held "
            "for a person; no refund until they check."
        )
    return (
        f"PayPal shows the payment as {paypal_status}. The case is {_state(case)}, so nothing "
        "changes."
    )
