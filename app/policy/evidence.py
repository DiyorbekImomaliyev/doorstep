"""PayPal dispute evidence (SPEC section 7a.4, DE-01 .. DE-04, ADR-038).

When a buyer has opened a PayPal dispute, PayPal is the judge in the middle: Doorstep does not
refund outside it (PO-05), it sends the evidence. The seller taps "Send evidence to PayPal";
this module records one EVIDENCE money action per decision (the unique decision+kind claim) and
an outbox job carrying the PayPal-Request-Id. The job calls provide-evidence; a retry reuses the
same request id and the same notes, so PayPal never gets the evidence twice. Only app.policy
calls the dispute write (hard rule 1).

The notes are built in app.cases.dispute_evidence from facts only (DE-02).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.orders.enums import CaseState, MoneyActionKind, MoneyActionStatus
from app.orders.models import Case, Dispute, MoneyAction, Order
from app.paypal_client.idempotency import request_id_for
from app.paypal_client.types import DisputeEvidence
from app.policy.ports import RefundGateway
from app.policy.service import (
    Approver,
    MissingRecord,
    PolicyError,
    PolicyService,
    PolicyStateError,
    _audit,
    _latest_decision,
)
from app.proof.receipts import ReceiptSigner, issue_receipt
from app.worker.models import Job
from app.worker.runner import enqueue
from app.worker.simulated import SIMULATED_STATUS, is_simulated

log = logging.getLogger("doorstep.policy.evidence")

DISPUTE_EVIDENCE_JOB = "paypal.dispute_evidence"  # payload: money action, dispute, evidence
SUBMITTED = "EVIDENCE_SUBMITTED"
NOTES_MAX = 2000  # PayPal's limit on evidence notes


class EvidenceSubmission(BaseModel):
    """What goes to PayPal: built in code from facts, never by a model (DE-02)."""

    evidence_type: Literal["PROOF_OF_FULFILLMENT"] = "PROOF_OF_FULFILLMENT"
    notes: str = Field(min_length=1, max_length=NOTES_MAX)
    carrier: str | None = None  # PayPal carrier code, e.g. ROYAL_MAIL; None -> OTHER
    tracking_number: str | None = None


def _open_dispute_row(session: Session, order_id: str) -> Dispute | None:
    return session.scalars(
        select(Dispute)
        .where(Dispute.order_id == order_id, Dispute.status == "open")
        .order_by(Dispute.opened_at.desc())  # pragma: no mutate - at most one open dispute
    ).first()


def existing_evidence(session: Session, decision_id: str) -> MoneyAction | None:
    return session.scalars(
        select(MoneyAction).where(
            MoneyAction.decision_id == decision_id,
            MoneyAction.kind == MoneyActionKind.EVIDENCE.value,
        )
    ).first()


def queue_dispute_evidence(
    session: Session,
    policy: PolicyService,
    case: Case,
    approver: Approver,
    submission: EvidenceSubmission,
) -> MoneyAction:
    """DE-01, DE-03: one EVIDENCE money action and one job per decision; a second tap returns
    the first action and sends nothing new."""
    if case.state != CaseState.DISPUTE_EVIDENCE.value:
        raise PolicyStateError(f"case {case.id}: evidence is sent only in DISPUTE_EVIDENCE")
    decision = _latest_decision(session, case.id)
    if decision is None:
        raise PolicyError(f"case {case.id}: no decision recorded")
    existing = existing_evidence(session, decision.id)
    if existing is not None:
        return existing
    order = session.get(Order, case.order_id)
    dispute = _open_dispute_row(session, case.order_id)
    if order is None or dispute is None:
        raise PolicyError(f"case {case.id}: no open PayPal dispute for this order")
    approval = policy._record_approval(case, decision, approver, decision.rule_id)
    now = policy.clock.now()
    ma = MoneyAction(
        order_id=order.id,
        case_id=case.id,
        decision_id=decision.id,
        seller_id=order.seller_id,
        kind=MoneyActionKind.EVIDENCE.value,
        amount_minor=0,  # evidence moves no money
        currency=order.currency,
        status=MoneyActionStatus.QUEUED.value,
        created_at=now,
    )
    with session.begin_nested():  # IntegrityError here = a concurrent tap won (PO-07)
        session.add(ma)
        session.flush()
        ma.request_id = request_id_for("dispute", dispute.paypal_dispute_id, "evidence", ma.id)
        session.flush()
    enqueue(
        session,
        DISPUTE_EVIDENCE_JOB,
        {
            "money_action_id": ma.id,
            "dispute_id": dispute.paypal_dispute_id,
            "evidence": submission.model_dump(),
        },
        clock=policy.clock,
        dedupe_key=f"evidence:{ma.id}",
    )
    _audit(
        session,
        actor=f"{approval.approver_kind}:{approval.approver_id}",
        action="evidence.queued",
        entity_type="money_action",
        entity_id=ma.id,
        before={},
        after={
            "money_action_id": ma.id,
            "dispute_id": dispute.paypal_dispute_id,
            "request_id": ma.request_id,
            "rule_id": decision.rule_id,
            "notes": submission.notes,
        },
        at=now,
    )
    return ma


class DisputeEvidenceJobHandler:
    """Executes DISPUTE_EVIDENCE_JOB: provide-evidence with the money action's request id."""

    def __init__(
        self, gateway: RefundGateway, clock: Clock, signer: ReceiptSigner | None = None
    ) -> None:
        self.gateway = gateway
        self.clock = clock
        self.signer = signer  # DE-04: each submission gets a signed receipt

    def _load(self, session: Session, job: Job) -> MoneyAction:
        ma_id = str(job.payload.get("money_action_id", ""))  # pragma: no mutate
        ma = session.scalar(select(MoneyAction).where(MoneyAction.id == ma_id).with_for_update())
        if ma is None:
            raise MissingRecord(f"job {job.id}: evidence money action missing")
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
            action=f"evidence.{status.value.lower()}",
            entity_type="money_action",
            entity_id=ma.id,
            before={"status": before},
            after={"status": ma.status, **after},
            at=self.clock.now(),
        )

    def run(self, session: Session, job: Job) -> None:
        ma = self._load(session, job)
        if ma.status != MoneyActionStatus.QUEUED.value:
            return  # sent, failed or blocked already: a redelivered job sends nothing (DE-03)
        # A missing id matches no dispute whatever the default, so the job is blocked.
        dispute_id = str(job.payload.get("dispute_id", ""))  # pragma: no mutate
        dispute = session.scalars(
            select(Dispute).where(Dispute.paypal_dispute_id == dispute_id)
        ).first()
        if dispute is None or dispute.status != "open":
            self._finish(
                session, ma, MoneyActionStatus.BLOCKED, {"blocked": "the dispute is not open"}
            )
            return
        evidence = DisputeEvidence.model_validate(job.payload.get("evidence") or {})
        self.gateway.provide_dispute_evidence(
            dispute_id, evidence=evidence, request_id=str(ma.request_id)
        )
        ma.paypal_status = SIMULATED_STATUS if is_simulated(dispute_id) else SUBMITTED
        self._finish(
            session,
            ma,
            MoneyActionStatus.COMPLETED,
            {"dispute_id": dispute_id, "paypal_status": ma.paypal_status},
        )
        if self.signer is not None and ma.case_id is not None:
            issue_receipt(
                session,
                self.signer,
                case_id=ma.case_id,
                kind="evidence",
                payload={
                    "money_action_id": ma.id,
                    "decision_id": ma.decision_id,
                    "dispute_id": dispute_id,
                    "request_id": ma.request_id,
                    "paypal_status": ma.paypal_status,
                    # The receipt proves exactly which notes PayPal received.
                    "notes_sha256": hashlib.sha256(evidence.notes.encode()).hexdigest(),
                },
                at=self.clock.now(),
            )
        log.info("dispute evidence %s sent for dispute %s", ma.id, dispute_id)

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        # A missing id matches no money action whatever the default: nothing to mark.
        ma_id = str(job.payload.get("money_action_id", ""))  # pragma: no mutate
        ma = session.get(MoneyAction, ma_id)
        if ma is None or ma.status != MoneyActionStatus.QUEUED.value:
            return
        self._finish(session, ma, MoneyActionStatus.FAILED, {"error": type(error).__name__})
