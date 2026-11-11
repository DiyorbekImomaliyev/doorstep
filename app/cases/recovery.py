"""Carrier recovery (RC-01 .. RC-06).

When the seller is liable to the buyer and the failure point is the carrier's (lost or damaged in
transit), Doorstep prepares a claim the seller can make against the carrier: the amount and the
deadline come from rules/carriers/carrier_claims_v1.yaml, the pack from code-built facts (an AI
draft replaces the template later, in a job, and is verified sentence by sentence).

Nothing here moves PayPal money: the seller submits the claim on the carrier's site and records
the outcome with one tap. Status changes only through RECOVERY_TRANSITIONS, with an audit row
for each change.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.explainer import money_text
from app.ai.recovery import ClaimPack, Fact, RecoveryDrafter, template
from app.cases.engine import FailurePoint
from app.cases.signals import EvidenceKind
from app.clock import Clock
from app.geo import postcode_district
from app.money import Currency, Money
from app.orders.enums import RecoveryStatus
from app.orders.models import (
    AuditLog,
    Case,
    Decision,
    Evidence,
    Order,
    Recovery,
    Shipment,
    TrackingEvent,
)
from app.orders.state import IllegalTransition
from app.policy.carriers import CarrierTerms, ClaimTerms
from app.worker.models import Job
from app.worker.runner import enqueue

R = RecoveryStatus
RECOVERY_TRANSITIONS: dict[RecoveryStatus, frozenset[RecoveryStatus]] = {
    R.PREPARED: frozenset({R.SUBMITTED, R.WITHDRAWN, R.EXPIRED}),
    R.SUBMITTED: frozenset({R.PAID, R.DENIED}),
    R.PAID: frozenset(),
    R.DENIED: frozenset(),
    R.WITHDRAWN: frozenset(),
    R.EXPIRED: frozenset(),
}
CARRIER_FAULTS = frozenset({FailurePoint.CARRIER_LOST, FailurePoint.DAMAGED_IN_TRANSIT})
DRAFT_RECOVERY = "draft_recovery"
DRAFT_RETRY_S = 300  # RC-08: a failed on-open draft waits this long before the model is asked again
SellerMark = Literal["submitted", "paid", "refused", "withdraw"]
_MARK_TO: dict[str, RecoveryStatus] = {
    "submitted": R.SUBMITTED,
    "paid": R.PAID,
    "refused": R.DENIED,
    "withdraw": R.WITHDRAWN,
}
_REPORT_TEXT = {
    "not_received": "not received",
    "damaged": "damaged",
    "missing_item": "missing an item",
    "empty_box": "arriving empty",
    "wrong_item": "the wrong item",
}
_MAX_EVENTS = 6


class RecoveryError(ValueError):
    """The seller's tap carries an impossible value (e.g. paid more than the claim)."""


def transition_recovery(
    session: Session,
    recovery: Recovery,
    to: RecoveryStatus,
    *,
    actor: str,
    reason: str,
    now: datetime,
) -> None:
    current = RecoveryStatus(recovery.status)
    if to not in RECOVERY_TRANSITIONS[current]:
        raise IllegalTransition(f"recovery {recovery.id}: {current} -> {to} is not allowed")
    recovery.status = to.value
    if to == R.SUBMITTED:
        recovery.submitted_at = now
    else:
        recovery.resolved_at = now
    session.add(
        AuditLog(
            actor=actor,
            action=f"recovery.{to.value.lower()}",
            entity_type="recovery",
            entity_id=recovery.id,
            before={"status": current.value},
            after={
                "status": to.value,
                "reason": reason,
                "recovered_minor": recovery.recovered_minor,
            },
            at=now,
        )
    )
    session.flush()


# ---------------------------------------------------------------- facts (code, never a model)


def _when(at: datetime) -> str:
    return at.strftime("%Y-%m-%d %H:%M UTC")


def _event_area(event: TrackingEvent) -> str:
    status = event.raw.get("tracking_status") if isinstance(event.raw, dict) else None
    location = status.get("location") if isinstance(status, dict) else None
    zip_code = location.get("zip") if isinstance(location, dict) else None
    return postcode_district(str(zip_code)) if zip_code else ""


def build_facts(
    session: Session,
    order: Order,
    case: Case,
    decision: Decision,
    shipment: Shipment,
    terms: ClaimTerms,
) -> list[Fact]:
    """Facts for the claim pack: districts only, never an address or the buyer's words."""
    facts: list[Fact] = []

    def add(kind: str, text: str) -> None:
        facts.append(Fact(id=f"F{len(facts) + 1}", kind=kind, text=text))

    currency = Currency(order.currency)

    def money(minor: int) -> str:
        return money_text(Money(amount_minor=minor, currency=currency))

    add(
        "payment",
        f"The buyer paid {money(order.amount_minor)} through PayPal for order "
        f"{order.id[:8].upper()}"
        + (f" ({order.item_description})." if order.item_description else "."),
    )
    add(
        "shipment",
        f"The parcel was handed to {'the carrier' if terms.is_default else terms.carrier_name} "
        "with tracking number "
        f"{shipment.tracking_number} on {_when(shipment.created_at)}.",
    )
    district = postcode_district(str((shipment.delivery_address or {}).get("postal_code") or ""))
    if district:
        add("destination", f"The destination postcode area is {district}.")
    events = session.scalars(
        select(TrackingEvent)
        .where(TrackingEvent.shipment_id == shipment.id)
        .order_by(TrackingEvent.occurred_at)
    ).all()
    for event in events[-_MAX_EVENTS:]:
        area = _event_area(event)
        add(
            "tracking",
            f"Carrier event: {event.status} at {_when(event.occurred_at)}"
            + (f" in {area}." if area else "."),
        )
    audit = decision.signals.get("scan_audit") or {}
    for check in audit.get("checks", []):
        if check.get("result") == "contradicts":
            add("scan_audit", f"The carrier's own data contradicts its scan: {check['finding']}.")
    liability = decision.signals.get("liability") or {}
    if liability.get("possession_proven") is False:
        add(
            "possession",
            "There is no signature, locker pickup code or seal scan proving the buyer received "
            "the parcel.",
        )
    packing = session.scalars(
        select(Evidence)
        .where(Evidence.order_id == order.id, Evidence.kind == EvidenceKind.PACKING_PHOTO.value)
        .order_by(Evidence.created_at.desc())
    ).first()
    if packing is not None and packing.ai_result.get("verdict") == "match":
        add("packing", "The packing photo taken before shipping matched the order.")
    add(
        "report",
        f"The buyer reported the parcel as {_REPORT_TEXT.get(case.reason, 'not received')} on "
        f"{_when(case.opened_at)}.",
    )
    cover = (
        f"standard cover is {money(terms.cover_minor)}"
        if terms.cover_minor is not None
        else "standard cover is not confirmed"
    )
    add(
        "terms",
        "The carrier's claim terms and standard cover are not confirmed."
        if terms.is_default
        else f"{terms.carrier_name} ({terms.service_basis}): {cover}.",
    )
    add(
        "claim",
        f"Claim amount: {money(terms.claim_minor)}. File by {_when(terms.deadline_at)} "
        f"({terms.window_days} days from {terms.window_starts}).",
    )
    return facts


# ---------------------------------------------------------------- prepare, mark, sweep


def prepare_recovery(
    session: Session,
    case: Case,
    decision: Decision,
    carriers: CarrierTerms,
    clock: Clock,
    *,
    draft: bool = True,
) -> Recovery | None:
    """RC-01: a recovery for a seller-liable carrier fault, at most one per case. `draft=False`
    keeps the fact-only pack and queues no model call (RC-08: cases the demo seed creates)."""
    liability = decision.signals.get("liability") or {}
    if liability.get("liable_party") != "seller":
        return None
    if decision.failure_point not in {fp.value for fp in CARRIER_FAULTS}:
        return None
    if session.scalars(select(Recovery).where(Recovery.case_id == case.id)).first() is not None:
        return None
    order = session.get(Order, case.order_id)
    shipment = session.scalars(
        select(Shipment).where(Shipment.order_id == case.order_id).order_by(Shipment.created_at)
    ).first()
    if order is None or shipment is None:
        return None
    delivered = session.scalars(
        select(TrackingEvent.occurred_at)
        .where(TrackingEvent.shipment_id == shipment.id, TrackingEvent.status == "DELIVERED")
        .order_by(TrackingEvent.occurred_at.desc())
    ).first()
    terms = carriers.claim(
        carrier=shipment.carrier,
        currency=Currency(order.currency),
        item_minor=order.amount_minor,
        damage=decision.failure_point == FailurePoint.DAMAGED_IN_TRANSIT.value,
        posted_at=shipment.created_at,
        delivered_at=delivered,
    )
    facts = build_facts(session, order, case, decision, shipment, terms)
    recovery = Recovery(
        case_id=case.id,
        order_id=order.id,
        seller_id=order.seller_id,
        decision_id=decision.id,
        carrier=shipment.carrier,
        carrier_id=terms.carrier_id,
        failure_point=decision.failure_point,
        claim_minor=terms.claim_minor,
        currency=order.currency,
        status=R.PREPARED.value,
        deadline_at=terms.deadline_at,
        terms=terms.model_dump(mode="json"),
        pack=template(facts).model_dump(mode="json"),
        created_at=clock.now(),
    )
    session.add(recovery)
    session.flush()
    session.add(
        AuditLog(
            actor="pipeline",
            action="recovery.prepared",
            entity_type="recovery",
            entity_id=recovery.id,
            before={},
            after={"status": R.PREPARED.value, "claim_minor": recovery.claim_minor},
            at=clock.now(),
        )
    )
    # The AI wording comes later from the worker, so the buyer's tap never waits on a model.
    if draft:
        enqueue(session, DRAFT_RECOVERY, {"recovery_id": recovery.id}, clock=clock)
    session.flush()
    return recovery


def seller_mark(
    session: Session,
    recovery: Recovery,
    mark: SellerMark,
    *,
    actor: str,
    now: datetime,
    amount_minor: int | None = None,
) -> Recovery:
    """RC-05: one tap. "paid" needs an amount from 1 up to the claim."""
    if mark == "paid":
        if amount_minor is None or not 1 <= amount_minor <= recovery.claim_minor:
            raise RecoveryError(f"the amount paid must be between 1 and {recovery.claim_minor}")
        recovery.recovered_minor = amount_minor
    transition_recovery(session, recovery, _MARK_TO[mark], actor=actor, reason=mark, now=now)
    return recovery


def sweep_expired(session: Session, now: datetime) -> list[Recovery]:
    """RC-06: prepared recoveries past their deadline expire (the cron sweep calls this)."""
    due = session.scalars(
        select(Recovery).where(Recovery.status == R.PREPARED.value, Recovery.deadline_at < now)
    ).all()
    for recovery in due:
        transition_recovery(
            session, recovery, R.EXPIRED, actor="sweep", reason="deadline passed", now=now
        )
    return list(due)


def recovered_totals(session: Session, seller_id: str) -> dict[str, int]:
    """Money the carriers paid back, per currency (RC-05)."""
    totals: dict[str, int] = defaultdict(int)
    for recovery in session.scalars(
        select(Recovery).where(Recovery.seller_id == seller_id, Recovery.status == R.PAID.value)
    ):
        totals[recovery.currency] += recovery.recovered_minor or 0
    return dict(totals)


# ---------------------------------------------------------------- AI wording (worker job)


def _drafted(recovery: Recovery) -> bool:
    return (recovery.pack or {}).get("source") == "model"


def draft_pack(session: Session, recovery: Recovery, drafter: RecoveryDrafter) -> bool:
    """The model rewrites a PREPARED recovery's fact-only pack; code verifies every sentence.
    True when the model's draft was kept (otherwise the pack stays built from the facts)."""
    if recovery.status != R.PREPARED.value or _drafted(recovery):
        return False  # already drafted, or no longer a claim to send: nothing to write
    pack = drafter.draft(ClaimPack.model_validate(recovery.pack).facts)
    recovery.pack = pack.model_dump(mode="json")
    session.flush()
    return pack.source == "model"


class DraftRecoveryHandler:
    """DRAFT_RECOVERY: the model rewrites the template pack; code verifies every sentence."""

    def __init__(self, drafter: RecoveryDrafter) -> None:
        self.drafter = drafter

    def run(self, session: Session, job: Job) -> None:
        recovery = session.get(Recovery, str(job.payload.get("recovery_id")))
        if recovery is not None:
            draft_pack(session, recovery, self.drafter)

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        """The template pack stays; nothing else to undo."""


class ClaimDraftOnOpen:
    """RC-08: AI where it is seen. A seeded case's claim pack is drafted by the model when the
    seller first opens it, then saved. One process-wide instance: two opens at once make one call,
    and a failed draft is not retried for DRAFT_RETRY_S (the fact-only pack stays meanwhile).
    No database connection is held while the model writes."""

    def __init__(self, drafter: RecoveryDrafter, clock: Clock) -> None:
        self.drafter = drafter
        self.clock = clock
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._failed_at: dict[str, float] = {}

    def _wanted(self, recovery: Recovery) -> bool:
        if recovery.status != R.PREPARED.value or _drafted(recovery):
            return False  # already drafted, or the claim was already sent: its text is final
        failed = self._failed_at.get(recovery.id)
        return failed is None or self.clock.monotonic() - failed >= DRAFT_RETRY_S

    def draft(self, session: Session, recovery: Recovery) -> Recovery:
        if not self._wanted(recovery):
            return recovery
        with self._guard:
            lock = self._locks.setdefault(recovery.id, threading.Lock())
        with lock:
            session.refresh(recovery)  # another open may have drafted it while this one waited
            if not self._wanted(recovery):
                return recovery
            facts = ClaimPack.model_validate(recovery.pack).facts
            session.commit()  # end the read before the model call
            pack = self.drafter.draft(facts)
            if pack.source != "model":
                self._failed_at[recovery.id] = self.clock.monotonic()
                return recovery
            self._failed_at.pop(recovery.id, None)
            session.refresh(recovery, with_for_update=True)  # the seller may have marked it
            if recovery.status == R.PREPARED.value and not _drafted(recovery):
                recovery.pack = pack.model_dump(mode="json")
            session.commit()
        return recovery
