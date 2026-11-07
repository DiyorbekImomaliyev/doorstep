"""A refund offer inside an open PayPal dispute (PP-09).

While a dispute is in PayPal's INQUIRY stage the seller may offer the buyer a refund there,
instead of waiting for PayPal to decide. The seller's tap records one OFFER money action per
decision and an outbox job. The offer itself moves no money: PayPal refunds only if the buyer
accepts, and that refund reaches Doorstep by webhook like any refund made outside it.

PayPal's docs do not say make-offer honours PayPal-Request-Id, so this module never lets a
retry send a second offer:

- before the POST, the job commits an attempt marker (`paypal_status = OFFER_ATTEMPTED`) and
  moves its own run time past a lease, so no other worker picks the job up meanwhile;
- a timeout or 5xx after that point is answered by reading the dispute back: an offer on record
  completes the action, no offer fails it as unconfirmed, and it is never sent again;
- a job that finds the marker on a fresh run (a crash after the POST) reads, never posts.

The note is a fixed template with no buyer text and no address. Only app.policy calls the
dispute write (hard rule 1).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.money import Currency, Money
from app.orders.enums import CaseState, MoneyActionKind, MoneyActionStatus
from app.orders.models import Case, Dispute, MoneyAction, Order
from app.paypal_client.errors import (
    PayPalAuthError,
    PayPalClientError,
    PayPalNotSent,
    PayPalServerError,
)
from app.paypal_client.idempotency import request_id_for
from app.paypal_client.types import DisputeDetail
from app.paypal_client.writes.money import OFFER_NOTE_MAX
from app.policy.evidence import _open_dispute_row
from app.policy.ports import OfferGateway
from app.policy.service import (
    _REFUNDABLE_CAPTURE,
    Approver,
    MissingRecord,
    PolicyError,
    PolicyService,
    PolicyStateError,
    _audit,
    _latest_decision,
    _remaining_refundable,
)
from app.proof.receipts import ReceiptSigner, issue_receipt
from app.worker.models import Job
from app.worker.runner import enqueue
from app.worker.simulated import SIMULATED_STATUS, is_simulated

log = logging.getLogger("doorstep.policy.offers")

DISPUTE_OFFER_JOB = "paypal.dispute_offer"  # payload: money action, dispute, note
ATTEMPTED = "OFFER_ATTEMPTED"  # committed before the POST: the offer may exist from here on
OFFERED = "OFFER_MADE"
UNCONFIRMED = "OFFER_UNCONFIRMED"  # sent, outcome unknown, PayPal shows none: check PayPal
OFFER_TYPE = "REFUND"  # the only offer type built; returns and replacements are not
INQUIRY = "INQUIRY"  # PayPal accepts make-offer only in this stage
OFFER_LEASE_S = 300  # no other worker runs the job while its POST may be in flight
_LIVE = (MoneyActionStatus.QUEUED.value, MoneyActionStatus.COMPLETED.value)
OFFER_MAYBE_MADE = (ATTEMPTED, UNCONFIRMED)  # a failed action with one of these may be live
_UNKNOWN_4XX = frozenset({408})  # Request Timeout: PayPal may have processed the request


class _Refused(Exception):
    """PayPal refused make-offer (4xx, 429, 401) or never saw it (PayPalNotSent): no offer."""

    def __init__(self, error: Exception) -> None:
        super().__init__()  # never shown: run() re-raises the PayPal error itself
        self.error = error


def offer_note(amount: Money, verify_url: str) -> str:
    """What the buyer reads in PayPal: fixed, neutral words (no admission, no accusation), the
    amount and the signed record. Never buyer text, never an address."""
    return (
        f"To resolve this case, the seller offers you a refund of {amount}. "
        f"Signed record of this offer: {verify_url}"
    )


def existing_offer(session: Session, decision_id: str) -> MoneyAction | None:
    return session.scalars(
        select(MoneyAction).where(
            MoneyAction.decision_id == decision_id,
            MoneyAction.kind == MoneyActionKind.OFFER.value,
        )
    ).first()


def _live_offer_on_order(session: Session, order_id: str) -> MoneyAction | None:
    """An offer that is on its way, made, or possibly made (sent with an unknown outcome): any
    of them stops another one, from any decision."""
    return session.scalars(
        select(MoneyAction).where(
            MoneyAction.order_id == order_id,
            MoneyAction.kind == MoneyActionKind.OFFER.value,
            or_(MoneyAction.status.in_(_LIVE), MoneyAction.paypal_status.in_(OFFER_MAYBE_MADE)),
        )
    ).first()


def _refundable_capture(order: Order) -> bool:
    return bool(order.capture_id) and order.capture_status in _REFUNDABLE_CAPTURE


def offer_amount(session: Session, case: Case) -> Money | None:
    """The refund the latest decision names, capped by what is still refundable. None when the
    decision moves no money (a reship) or nothing is left."""
    decision = _latest_decision(session, case.id)
    if decision is None or not decision.refund_amount_minor:
        return None
    order = session.get(Order, case.order_id)
    assert order is not None  # cases.order_id is a foreign key
    if not _refundable_capture(order):
        return None
    left = _remaining_refundable(session, order).amount_minor
    minor = min(decision.refund_amount_minor, left)
    return Money(amount_minor=minor, currency=Currency(order.currency)) if minor > 0 else None


def queue_dispute_offer(
    session: Session, policy: PolicyService, case: Case, approver: Approver, *, verify_url: str
) -> MoneyAction:
    """One OFFER money action and one job per decision; a second tap returns the first.

    A seller tap authorises it, like a seller approval: the hold checks, the auto-approve limit
    and the daily cap govern automatic refunds and do not apply."""
    # The case row lock makes concurrent taps queue one after another (as approve does), so a
    # second tap sees the first offer instead of racing it.
    session.refresh(case, with_for_update=True)  # pragma: no mutate - only Postgres locks rows
    if case.state != CaseState.DISPUTE_EVIDENCE.value:
        raise PolicyStateError(f"case {case.id}: an offer is made only in DISPUTE_EVIDENCE")
    decision = _latest_decision(session, case.id)
    if decision is None:
        raise PolicyError(f"case {case.id}: no decision recorded")
    existing = existing_offer(session, decision.id)
    if existing is not None:
        return existing
    order = session.get(Order, case.order_id)
    dispute = _open_dispute_row(session, case.order_id)
    if order is None or dispute is None:
        raise PolicyError(f"case {case.id}: no open PayPal dispute for this order")
    if _live_offer_on_order(session, order.id) is not None:
        raise PolicyStateError(f"case {case.id}: an offer was already made on this dispute")
    amount = offer_amount(session, case)
    if amount is None:
        raise PolicyError(f"case {case.id}: this decision refunds nothing")
    approval = policy._record_approval(case, decision, approver, decision.rule_id)
    now = policy.clock.now()
    ma = MoneyAction(
        order_id=order.id,
        case_id=case.id,
        decision_id=decision.id,
        seller_id=order.seller_id,
        kind=MoneyActionKind.OFFER.value,
        amount_minor=amount.amount_minor,
        currency=order.currency,
        status=MoneyActionStatus.QUEUED.value,
        created_at=now,
    )
    with session.begin_nested():  # IntegrityError here = a concurrent tap won (PO-07)
        session.add(ma)
        session.flush()
        ma.request_id = request_id_for("dispute", dispute.paypal_dispute_id, "offer", ma.id)
        session.flush()
    note = offer_note(amount, verify_url)
    enqueue(
        session,
        DISPUTE_OFFER_JOB,
        {"money_action_id": ma.id, "dispute_id": dispute.paypal_dispute_id, "note": note},
        clock=policy.clock,
        dedupe_key=f"offer:{ma.id}",
    )
    _audit(
        session,
        actor=f"{approval.approver_kind}:{approval.approver_id}",
        action="offer.queued",
        entity_type="money_action",
        entity_id=ma.id,
        before={},
        after={
            "money_action_id": ma.id,
            "dispute_id": dispute.paypal_dispute_id,
            "request_id": ma.request_id,
            "rule_id": decision.rule_id,
            "amount_minor": ma.amount_minor,
            "currency": ma.currency,
            "note": note,
        },
        at=now,
    )
    return ma


class OfferJobHandler:
    """Executes DISPUTE_OFFER_JOB: re-check the dispute at PayPal, then make the offer once."""

    def __init__(
        self, gateway: OfferGateway, clock: Clock, signer: ReceiptSigner | None = None
    ) -> None:
        self.gateway = gateway
        self.clock = clock
        self.signer = signer

    def _load(self, session: Session, job: Job) -> MoneyAction:
        # A missing id matches no money action whatever the default: the job fails the same way.
        ma_id = str(job.payload.get("money_action_id", ""))  # pragma: no mutate
        ma = session.scalar(select(MoneyAction).where(MoneyAction.id == ma_id).with_for_update())
        if ma is None:
            raise MissingRecord(f"job {job.id}: offer money action missing")
        return ma

    def _finish(
        self, session: Session, ma: MoneyAction, status: MoneyActionStatus, after: dict[str, Any]
    ) -> None:
        before = ma.status
        ma.status = status.value
        ma.paypal_response = after
        ma.completed_at = self.clock.now()
        _audit(
            session,
            actor="paypal" if status == MoneyActionStatus.COMPLETED else "policy",
            action=f"offer.{status.value.lower()}",
            entity_type="money_action",
            entity_id=ma.id,
            before={"status": before},
            after={"status": ma.status, **after},
            at=self.clock.now(),
        )

    def _blocked_by(self, session: Session, ma: MoneyAction, detail: DisputeDetail) -> str | None:
        """Why PayPal's dispute, as it is now, does not take this offer; None when it does.
        Lowers the amount to what is still refundable and to the disputed amount."""
        order = session.get(Order, ma.order_id)
        assert order is not None  # money_actions.order_id is a foreign key
        if not _refundable_capture(order):
            return "the payment can no longer be refunded"
        if detail.status == "RESOLVED":
            return "PayPal shows the dispute resolved"
        if order.capture_id not in detail.capture_ids:
            return "the dispute names another payment"
        if detail.seller_offer_made:
            return "an offer was already made on this dispute"
        if detail.stage != INQUIRY:
            return f"PayPal takes offers only in the inquiry stage, not {detail.stage or 'unknown'}"
        if OFFER_TYPE not in detail.offer_types:
            return "PayPal does not allow a refund offer on this dispute"
        caps = [ma.amount_minor, _remaining_refundable(session, order).amount_minor]
        if detail.amount is not None:
            if detail.amount.currency.value != order.currency:
                return "the dispute is in another currency"
            caps.append(detail.amount.amount_minor)
        if min(caps) <= 0:
            return "nothing is left to refund"
        if min(caps) < ma.amount_minor:
            _audit(
                session,
                actor="policy",
                action="offer.lowered",
                entity_type="money_action",
                entity_id=ma.id,
                before={"amount_minor": ma.amount_minor},
                after={"amount_minor": min(caps)},
                at=self.clock.now(),
            )
            ma.amount_minor = min(caps)
        return None

    def run(self, session: Session, job: Job) -> None:
        ma = self._load(session, job)
        if ma.status != MoneyActionStatus.QUEUED.value:
            return  # made, failed or blocked already: a redelivered job sends nothing
        # A missing id matches no dispute whatever the default, so the job is blocked.
        dispute_id = str(job.payload.get("dispute_id", ""))  # pragma: no mutate
        if ma.paypal_status == ATTEMPTED:
            # An earlier run may have reached PayPal and then died: read, never post again.
            self._settle_from_paypal(session, ma, dispute_id)
            return
        note = str(job.payload.get("note") or "")
        if not 0 < len(note) <= OFFER_NOTE_MAX:
            self._finish(session, ma, MoneyActionStatus.BLOCKED, {"blocked": "no valid note"})
            return
        dispute = session.scalars(
            select(Dispute).where(Dispute.paypal_dispute_id == dispute_id)
        ).first()
        if dispute is None or dispute.status != "open":
            self._finish(
                session, ma, MoneyActionStatus.BLOCKED, {"blocked": "the dispute is not open"}
            )
            return
        reason = self._blocked_by(session, ma, self.gateway.get_dispute(dispute_id))
        if reason is not None:
            self._finish(session, ma, MoneyActionStatus.BLOCKED, {"blocked": reason})
            return
        # The claim: from this commit on, the offer may exist at PayPal. The lease keeps any
        # other worker off the job while the POST is in flight.
        ma.paypal_status = ATTEMPTED
        job.run_at = self.clock.now() + timedelta(seconds=OFFER_LEASE_S)
        session.commit()
        try:
            made = self._post(ma, dispute_id, note)
            if made:
                self._complete(session, ma, dispute_id, note)
            else:
                self._settle_from_paypal(session, ma, dispute_id)
        except _Refused as refused:  # PayPal made no offer: the marker goes
            session.rollback()
            ma.paypal_status = None
            self._release(session, job)
            raise refused.error from None  # 4xx fails in on_failed; 429 and 401 retry
        except Exception:  # outcome unknown: the marker stays, so the retry only reads
            session.rollback()
            self._release(session, job)
            raise

    def _post(self, ma: MoneyAction, dispute_id: str, note: str) -> bool:
        """True: PayPal made the offer. False: the outcome is unknown and the offer may exist."""
        try:
            self.gateway.make_dispute_offer(
                dispute_id,
                offer=Money(amount_minor=ma.amount_minor, currency=Currency(ma.currency)),
                note=note,
                request_id=str(ma.request_id),
            )
        except PayPalNotSent as error:  # no token or no connection: PayPal never saw it
            raise _Refused(error) from error
        except PayPalServerError:  # 5xx or timeout after sending
            return False
        except PayPalClientError as error:
            if error.status in _UNKNOWN_4XX:
                return False
            raise _Refused(error) from error  # PayPal answered no
        except PayPalAuthError as error:  # PayPal refused the token: nothing was processed
            raise _Refused(error) from error
        return True

    def _release(self, session: Session, job: Job) -> None:
        """Hand the job back to the runner now, so it records the failure and its backoff."""
        job.run_at = self.clock.now()
        session.commit()

    def _settle_from_paypal(self, session: Session, ma: MoneyAction, dispute_id: str) -> None:
        """After an unknown outcome: PayPal's own record decides. Never posts."""
        if self.gateway.get_dispute(dispute_id).seller_offer_made:
            self._complete(session, ma, dispute_id, None)
            return
        ma.paypal_status = UNCONFIRMED
        self._finish(
            session,
            ma,
            MoneyActionStatus.FAILED,
            {"error": "unconfirmed: PayPal shows no offer; it is not sent again"},
        )

    def _complete(
        self, session: Session, ma: MoneyAction, dispute_id: str, note: str | None
    ) -> None:
        """Mark the offer made and sign it. note is None when PayPal's record was read back."""
        ma.paypal_status = SIMULATED_STATUS if is_simulated(dispute_id) else OFFERED
        self._finish(
            session,
            ma,
            MoneyActionStatus.COMPLETED,
            {
                "dispute_id": dispute_id,
                "paypal_status": ma.paypal_status,
                "amount_minor": ma.amount_minor,
            },
        )
        if self.signer is not None and ma.case_id is not None:
            issue_receipt(
                session,
                self.signer,
                case_id=ma.case_id,
                kind="offer",
                payload={
                    "money_action_id": ma.id,
                    "decision_id": ma.decision_id,
                    "dispute_id": dispute_id,
                    "request_id": ma.request_id,
                    "amount_minor": ma.amount_minor,
                    "currency": ma.currency,
                    "paypal_status": ma.paypal_status,
                    # The receipt proves which note PayPal received (None: read back, not sent now)
                    "note_sha256": hashlib.sha256(note.encode()).hexdigest() if note else None,
                },
                at=self.clock.now(),
            )
        log.info("refund offer %s made on dispute %s", ma.id, dispute_id)

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        # A missing id matches no money action whatever the default: nothing to mark.
        ma_id = str(job.payload.get("money_action_id", ""))  # pragma: no mutate
        ma = session.get(MoneyAction, ma_id)
        if ma is None or ma.status != MoneyActionStatus.QUEUED.value:
            return
        self._finish(session, ma, MoneyActionStatus.FAILED, {"error": type(error).__name__})
