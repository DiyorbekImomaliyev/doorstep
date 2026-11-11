"""MN-02..MN-08: the Money desk, the seller's ledger of every PayPal write Doorstep made.

Read only, seller link only, like app/api/ledger.py. No PayPal call is made here: each figure is
read from rows Doorstep already stored. The money action's audit rows give its lifecycle (queued,
PayPal's answer, completed, failed or blocked); its outbox job shows how often it was tried, always
with one PayPal-Request-Id; its signed receipts are returned whole so the page can check them
against the public `GET /cases/{id}/verify`.

Rule 9: no buyer name, address, email, payer id or buyer text, and no free text the seller or the
model wrote (evidence notes, offer notes). Actors are named by role ("You", "PayPal"), never by id.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.autopilot import seller_from_link
from app.api.deps import AppDeps, Services
from app.api.journey import Db
from app.api.views import money_status
from app.orders.enums import CaseState, MoneyActionKind, MoneyActionStatus
from app.orders.models import Approval, AuditLog, Case, Decision, MoneyAction, Order, Recovery
from app.policy.autopilot import RULE_APPROVER
from app.policy.reasons import Reason
from app.proof.models import Receipt
from app.worker.models import Job
from app.worker.simulated import SIMULATED_STATUS

_REFUNDS = (MoneyActionKind.REFUND.value, MoneyActionKind.PARTIAL_REFUND.value)
_IN_FLIGHT = (MoneyActionStatus.QUEUED.value, MoneyActionStatus.PENDING.value)
_STOPPED = (MoneyActionStatus.BLOCKED.value, MoneyActionStatus.FAILED.value)

# The pre-refund re-check (RefundJobHandler._recheck), as the seller reads it: each check, and the
# reason codes that mean it stopped the refund. A refund only reaches PayPal once all have passed.
RECHECKS: tuple[tuple[str, frozenset[Reason]], ...] = (
    ("Case still open", frozenset({Reason.CASE_FINISHED})),
    (
        "No dispute",
        frozenset(
            {
                Reason.DISPUTE_OPENED,
                Reason.DISPUTE_CHECK_UNAVAILABLE,
                Reason.UNMATCHED_DISPUTE,
                Reason.DISPUTE_CHECK_REFUSED,
            }
        ),
    ),
    ("Evidence unchanged", frozenset({Reason.EVIDENCE_CHANGED})),
    (
        "Capture refundable",
        frozenset(
            {
                Reason.NO_PAYPAL_CAPTURE,
                Reason.CAPTURE_CURRENCY_MISMATCH,
                Reason.CAPTURE_NOT_REFUNDABLE,
            }
        ),
    ),
    (
        "Within the capture",
        frozenset({Reason.UNKNOWN_REFUND_AT_PAYPAL, Reason.WOULD_EXCEED_CAPTURE}),
    ),
)

_RECEIPT_KIND = {
    "approve": "Approval",
    "make-offer": "Offer approval",
    "send-evidence": "Evidence approval",
    "refund": "Refund",
    "offer": "Offer",
    "evidence": "Evidence",
}
# Receipts signed when PayPal confirmed the write (the others record the approval).
_PAID_RECEIPTS = frozenset({"refund", "offer", "evidence"})

_NOUNS = {"refund": "Refund", "offer": "Offer", "evidence": "Evidence"}
_ACTORS = {
    "paypal": "PayPal",
    "policy": "Doorstep re-check",
    "worker": "Doorstep worker",
    "reviewer": "Reviewer",
}


class DeskRow(BaseModel):
    """One money action: the fields of the list's MoneyRow plus what the desk needs."""

    action_id: str
    case_id: str | None
    order_id: str
    order_ref: str
    item: str
    kind: str
    amount_minor: int
    currency: str
    status: str  # completed | pending | failed | queued | blocked | simulated | unconfirmed
    paypal_refund_id: str | None
    request_id: str | None
    created_at: datetime
    completed_at: datetime | None
    external: bool
    simulated: bool
    receipts: int


class RecoveryFlow(BaseModel):
    """Money a carrier paid back on a claim (RC-05): the desk's money in."""

    recovery_id: str
    order_ref: str
    carrier_name: str
    recovered_minor: int
    currency: str
    paid_at: datetime


class MoneyDesk(BaseModel):
    rows: list[DeskRow]
    recoveries: list[RecoveryFlow]
    as_of: datetime


class Step(BaseModel):
    at: datetime
    step: str  # approved | queued | rechecked | pending | completed | simulated | failed | ...
    label: str
    actor: str
    rule_id: str | None = None
    rule_version: str | None = None
    approver: str | None = None
    detail: str | None = None


class Outbox(BaseModel):
    status: str  # queued | done | failed
    attempts: int
    created_at: datetime


class Stop(BaseModel):
    title: str
    text: str
    check: str | None = None


class SignedReceipt(BaseModel):
    seq: int
    kind: str
    label: str
    hash: str
    signature: str
    signed_at: datetime
    paid: bool  # signed when PayPal confirmed the write, not at the approval


class MoneyDetail(BaseModel):
    action_id: str
    case_id: str | None
    case_state: str | None
    kind: str
    status: str
    amount_minor: int
    currency: str
    simulated: bool
    external: bool
    request_id: str | None
    paypal_refund_id: str | None
    paypal_status: str | None
    rule_id: str | None
    rule_version: str | None
    approver: str | None
    outbox: Outbox | None
    checks: list[str]  # the re-checks this refund passed right before PayPal; empty if not sent
    found_at_paypal: bool  # an earlier attempt had landed: recorded, never sent again
    stop: Stop | None
    timeline: list[Step]
    receipts: list[SignedReceipt]
    public_key: str
    can_redecide: bool


def _who(actor: str) -> str:
    """An audit actor or approver ("seller:<id>", "system:policy", "paypal") by role only."""
    kind, _, ident = actor.partition(":")
    if kind == "seller":
        return "You"
    if kind == "system":
        return "Your autopilot rule" if ident.startswith(RULE_APPROVER) else "Doorstep policy"
    return _ACTORS.get(kind, kind.replace("-", " ").capitalize())


def _approver(approval: Approval | None) -> str | None:
    if approval is None:
        return None
    return _who(f"{approval.approver_kind}:{approval.approver_id}")


def _job_key(ma: MoneyAction) -> str:
    return f"{'refund' if ma.kind in _REFUNDS else ma.kind}:{ma.id}"


def _is_simulated(ma: MoneyAction) -> bool:
    return ma.paypal_status == SIMULATED_STATUS or str(ma.paypal_refund_id or "").startswith("SIM-")


def _sentence(text: str) -> str:
    text = text.strip()
    return (text[:1].upper() + text[1:]).rstrip(".") + "." if text else ""


def _check_for(code: str | None) -> str | None:
    for label, codes in RECHECKS:
        if code in {c.value for c in codes}:
            return label
    return None


def _failure(ma: MoneyAction) -> Stop:
    """Why a write did not complete, in plain words; never a stack trace."""
    response: dict[str, Any] = ma.paypal_response or {}
    error = str(response.get("error") or "")
    if ma.kind not in _REFUNDS:
        if ma.paypal_status and money_status(ma) == "unconfirmed":
            return Stop(
                title="Not confirmed",
                text="Sent once; PayPal shows no offer. It is never sent again.",
            )
        return Stop(title="Not sent", text=_sentence(error) or "PayPal did not take it.")
    if ma.paypal_status:
        return Stop(
            title="PayPal declined",
            text=f"PayPal answered {ma.paypal_status}. No money moved; you decide next.",
        )
    kind = error.partition(":")[0]
    if kind in ("PayPalServerError", "TimeoutError", "ReadTimeout", "ConnectTimeout"):
        title = "PayPal did not answer"
    elif kind == "PayPalClientError":
        title = "PayPal refused it"
    elif kind == "MissingRecord":
        title = "A record is missing"
    else:
        title = "Not sent"
    return Stop(
        title=title, text="Every retry used the same PayPal-Request-Id, so nothing was paid twice."
    )


def _stop(ma: MoneyAction) -> Stop | None:
    status = money_status(ma)
    response: dict[str, Any] = ma.paypal_response or {}
    if status == MoneyActionStatus.BLOCKED.value:
        code = response.get("blocked_code")
        text = _sentence(str(response.get("blocked") or "the re-check stopped it"))
        return Stop(
            title="Stopped before PayPal",
            text=f"{text} No money moved.",
            check=_check_for(str(code) if code else None),
        )
    if status in (MoneyActionStatus.FAILED.value, "unconfirmed"):
        return _failure(ma)
    return None


def _label(action: str, after: dict[str, Any], actor: str) -> tuple[str, str]:
    """(step, label) for one audit row on a money action."""
    kind, _, verb = action.partition(".")
    answer = after.get("paypal_status") or (after.get("paypal_response") or {}).get("status")
    simulated = answer == SIMULATED_STATUS
    noun = _NOUNS.get(kind, kind.capitalize())
    if verb == "queued":
        return "queued", "Queued in outbox"
    if verb == "pending":
        return "pending", "PayPal: pending"
    if verb == "completed":
        if simulated:
            return "simulated", f"{noun} simulated"
        if kind == "refund":
            return "completed", "PayPal: completed"
        return "completed", "Offer made" if kind == "offer" else "Evidence sent"
    if verb == "failed":
        return "failed", "PayPal: failed" if actor == "paypal" else "Not sent"
    if verb == "blocked":
        return "blocked", "Stopped by re-check" if kind == "refund" else f"{noun} not sent"
    if verb == "external":
        return "external", "Refunded at PayPal"
    if verb == "lowered":
        return "lowered", "Offer lowered"
    return verb or action, action


def _timeline(
    s: Session,
    ma: MoneyAction,
    decision: Decision | None,
    approval: Approval | None,
    receipts: list[Receipt],
) -> list[Step]:
    """The money action's lifecycle: approval, audit rows and signed receipts, oldest first."""
    steps: list[tuple[datetime, int, Step]] = []
    rule_id = decision.rule_id if decision else None
    rule_version = decision.rule_version if decision else None
    if approval is not None:
        steps.append(
            (
                approval.at,
                0,
                Step(
                    at=approval.at,
                    step="approved",
                    label="Approved",
                    actor=_who(f"{approval.approver_kind}:{approval.approver_id}"),
                    rule_id=approval.rule_id or rule_id,
                    rule_version=rule_version,
                    approver=_approver(approval),
                ),
            )
        )
    rows = s.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "money_action", AuditLog.entity_id == ma.id)
        .order_by(AuditLog.at, AuditLog.id)
    ).all()
    for row in rows:
        after: dict[str, Any] = row.after or {}
        step, label = _label(row.action, after, row.actor)
        response: dict[str, Any] = after.get("paypal_response") or {}
        refund_id = response.get("refund_id") or after.get("paypal_refund_id")
        detail: str | None = None
        if step == "queued":
            detail = "PayPal-Request-Id set"
        elif step in ("completed", "simulated", "pending", "failed") and refund_id:
            detail = str(refund_id)
        elif step == "blocked":
            detail = _sentence(str(response.get("blocked") or after.get("blocked") or ""))
            detail = detail.rstrip(".") or None
        elif step == "external":
            detail = "Made outside Doorstep"
        sent = row.action in ("refund.completed", "refund.pending") or (
            row.action == "refund.failed" and row.actor == "paypal"
        )
        if sent and row.before.get("status") == MoneyActionStatus.QUEUED.value:
            steps.append(
                (
                    row.at,
                    1,
                    Step(
                        at=row.at,
                        step="rechecked",
                        label="Found at PayPal" if response.get("recovered") else "Re-checked live",
                        actor="Doorstep re-check",
                        detail="Not sent again"
                        if response.get("recovered")
                        else f"{len(RECHECKS)} checks passed",
                    ),
                )
            )
        steps.append(
            (
                row.at,
                0 if step == "queued" else 2,
                Step(
                    at=row.at,
                    step=step,
                    label=label,
                    actor=_who(row.actor),
                    rule_id=after.get("rule_id") if step == "queued" else None,
                    rule_version=after.get("rule_version") if step == "queued" else None,
                    detail=detail,
                ),
            )
        )
    for r in receipts:
        steps.append(
            (
                r.created_at,
                3,
                Step(
                    at=r.created_at,
                    step="signed",
                    label=f"Signed · #{r.seq}",
                    actor="Doorstep",
                    detail=_RECEIPT_KIND.get(r.kind, r.kind.capitalize()),
                ),
            )
        )
    steps.sort(key=lambda t: (t[0], t[1]))
    return [step for _, _, step in steps]


def _receipts_for(s: Session, case_ids: list[str]) -> dict[str, list[Receipt]]:
    """Signed receipts that name a money action, by that money action's id, oldest first."""
    out: dict[str, list[Receipt]] = {}
    if not case_ids:
        return out
    for r in s.scalars(select(Receipt).where(Receipt.case_id.in_(case_ids)).order_by(Receipt.seq)):
        ma_id = (r.payload or {}).get("money_action_id")
        if ma_id:
            out.setdefault(str(ma_id), []).append(r)
    return out


def _seller_action(s: Session, seller_id: str, action_id: str) -> MoneyAction:
    ma = s.get(MoneyAction, action_id)
    if ma is None or ma.seller_id != seller_id:
        raise HTTPException(404, "not found")
    return ma


def build_money_desk_router(deps: AppDeps, services: Services) -> APIRouter:
    router = APIRouter()
    base = "/api/seller/{token}/money-desk"

    @router.get(base, response_model=MoneyDesk)
    def desk(token: str, s: Db) -> MoneyDesk:
        seller = seller_from_link(s, services, token)
        orders = {o.id: o for o in s.scalars(select(Order).where(Order.seller_id == seller.id))}
        actions = list(
            s.scalars(
                select(MoneyAction)
                .where(MoneyAction.seller_id == seller.id)
                .order_by(MoneyAction.created_at.desc(), MoneyAction.id)
            )
        )
        receipts = _receipts_for(s, sorted({m.case_id for m in actions if m.case_id}))
        rows = [
            DeskRow(
                action_id=m.id,
                case_id=m.case_id,
                order_id=m.order_id,
                order_ref=m.order_id[:8].upper(),
                item=orders[m.order_id].item_description if m.order_id in orders else "",
                kind=m.kind,
                amount_minor=m.amount_minor,
                currency=m.currency,
                status=money_status(m),
                paypal_refund_id=m.paypal_refund_id,
                request_id=m.request_id,
                created_at=m.created_at,
                completed_at=m.completed_at,
                external=m.external,
                simulated=_is_simulated(m),
                receipts=len(receipts.get(m.id, [])),
            )
            for m in actions
        ]
        paid = s.scalars(
            select(Recovery)
            .where(Recovery.seller_id == seller.id, Recovery.status == "PAID")
            .order_by(Recovery.resolved_at, Recovery.id)
        )
        recoveries = [
            RecoveryFlow(
                recovery_id=r.id,
                order_ref=r.order_id[:8].upper(),
                carrier_name=str((r.terms or {}).get("carrier_name") or r.carrier),
                recovered_minor=r.recovered_minor or 0,
                currency=r.currency,
                paid_at=r.resolved_at or r.submitted_at or r.created_at,
            )
            for r in paid
        ]
        return MoneyDesk(rows=rows, recoveries=recoveries, as_of=deps.clock.now())

    @router.get(f"{base}/{{action_id}}", response_model=MoneyDetail)
    def detail(token: str, action_id: str, s: Db) -> MoneyDetail:
        seller = seller_from_link(s, services, token)
        ma = _seller_action(s, seller.id, action_id)
        case = s.get(Case, ma.case_id) if ma.case_id else None
        decision = s.get(Decision, ma.decision_id) if ma.decision_id else None
        approval = (
            s.scalars(
                select(Approval)
                .where(Approval.decision_id == decision.id)
                .order_by(Approval.at.desc())
            ).first()
            if decision
            else None
        )
        receipts = _receipts_for(s, [ma.case_id] if ma.case_id else []).get(ma.id, [])
        job = s.scalars(select(Job).where(Job.dedupe_key == _job_key(ma))).first()
        status = money_status(ma)
        response: dict[str, Any] = ma.paypal_response or {}
        sent = ma.kind in _REFUNDS and not ma.external and bool(ma.paypal_status)
        in_flight = case is not None and bool(
            s.scalar(
                select(MoneyAction.id).where(
                    MoneyAction.case_id == case.id, MoneyAction.status.in_(_IN_FLIGHT)
                )
            )
        )
        can_redecide = (
            case is not None
            and ma.kind in _REFUNDS
            and ma.status in _STOPPED
            and case.state == CaseState.HOLD_FOR_REVIEW.value
            and not in_flight
        )
        return MoneyDetail(
            action_id=ma.id,
            case_id=ma.case_id,
            case_state=case.state if case else None,
            kind=ma.kind,
            status=status,
            amount_minor=ma.amount_minor,
            currency=ma.currency,
            simulated=_is_simulated(ma),
            external=ma.external,
            request_id=ma.request_id,
            paypal_refund_id=ma.paypal_refund_id,
            paypal_status=ma.paypal_status,
            rule_id=(approval.rule_id if approval else None)
            or (decision.rule_id if decision else None),
            rule_version=decision.rule_version if decision else None,
            approver=_approver(approval),
            outbox=Outbox(status=job.status, attempts=job.attempts, created_at=job.created_at)
            if job
            else None,
            checks=[label for label, _ in RECHECKS] if sent else [],
            found_at_paypal=bool(response.get("recovered")),
            stop=_stop(ma),
            timeline=_timeline(s, ma, decision, approval, receipts),
            receipts=[
                SignedReceipt(
                    seq=r.seq,
                    kind=r.kind,
                    label=_RECEIPT_KIND.get(r.kind, r.kind.capitalize()),
                    hash=r.hash,
                    signature=r.signature,
                    signed_at=r.created_at,
                    paid=r.kind in _PAID_RECEIPTS,
                )
                for r in receipts
            ],
            public_key=services.receipts.public_key_b64,
            can_redecide=can_redecide,
        )

    return router
