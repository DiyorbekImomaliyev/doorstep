"""DS-02 .. DS-07: the seller's PayPal disputes desk, one read-only view per dispute.

Everything here is read from rows Doorstep already stored; no PayPal call is made and nothing is
written. PayPal facts (reason, status, stage, the seller's reply-by date, the disputed amount and
the outcome code) come only from the dispute webhooks PayPal sent, word for word; a field PayPal
did not send is None, never guessed. The seeded demo dispute has no webhook, so it has none.

Rule 9: no buyer name, email, address, payer id or buyer text. A webhook payload carries the
buyer's name and messages; only the named fields below are read from it. Addresses are compared
in code for the Seller Protection check and never returned.

The Seller Protection checklist is what Doorstep can verify from its rows, against PayPal's
conditions for an item-not-received claim: PayPal's own verdict on the payment (information only,
as in app.proof.seller_protection), shipped to the address on the PayPal order (the fields
rules/paypal_sp_v1.yaml compares), the carrier's delivered scan, tracking on the PayPal payment,
and a reply in the dispute. Each check is "pass", "partial", "fail" or "missing"; a check with no
stored fact is "missing", never "pass".
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.autopilot import seller_from_link
from app.api.deps import AppDeps, Services
from app.api.journey import Db
from app.api.views import latest_decision, money_status
from app.cases.dispute_intake import READ_ACTION
from app.money import Money
from app.orders.enums import CaseState, MoneyActionKind, MoneyActionStatus
from app.orders.models import AuditLog, Case, Dispute, MoneyAction, Order, Shipment, TrackingEvent
from app.orders.schemas import Address
from app.policy.offers import OFFER_MAYBE_MADE, offer_amount
from app.policy.rules import RuleBook
from app.proof.models import Receipt
from app.proof.seller_protection import ACTION as PROTECTION_ACTION
from app.proof.seller_protection import latest_reports
from app.worker.kinds import ADD_TRACKING
from app.worker.models import Job, WebhookInbox
from app.worker.simulated import SIMULATED_STATUS, is_simulated

Stage = Literal["needs_reply", "evidence", "offer", "resolved"]
CheckState = Literal["pass", "partial", "fail", "missing"]
Result = Literal["won", "lost", "other"]

DISPUTE_EVENTS = (
    "CUSTOMER.DISPUTE.CREATED",
    "CUSTOMER.DISPUTE.UPDATED",
    "CUSTOMER.DISPUTE.RESOLVED",
)
_OUTCOME_PREFIX = "transition: dispute resolved: "
# PayPal's dispute_outcome.outcome_code values (Disputes API v1). Only these two sides count as a
# win or a loss; any other code (RESOLVED_WITH_PAYOUT, ACCEPTED, DENIED, NONE) is shown as PayPal
# sent it and counted as neither.
_WON = frozenset({"RESOLVED_SELLER_FAVOUR", "CANCELED_BY_BUYER"})
_LOST = frozenset({"RESOLVED_BUYER_FAVOUR"})
_VERDICT: dict[str, CheckState] = {
    "ELIGIBLE": "pass",
    "PARTIALLY_ELIGIBLE": "partial",
    "NOT_ELIGIBLE": "fail",
}
_SENT = (MoneyActionStatus.COMPLETED.value, "simulated")
_EVIDENCE_RECEIPTS = ("send-evidence", "evidence", "make-offer")
_DISPUTE_AGENT_STEPS = {
    "list_disputes": "Agent checked PayPal disputes",
    "get_dispute": "Agent read the PayPal dispute",
    "record_dispute": "Agent found the open dispute",
}
_CASE_STEPS = {
    CaseState.OPEN.value: "Problem reported",
    CaseState.DECIDED.value: "Doorstep decided",
    CaseState.DISPUTE_EVIDENCE.value: "Case moved to the dispute",
    CaseState.HOLD_FOR_REVIEW.value: "Held for a person",
    CaseState.SETTLED.value: "Case settled",
    CaseState.CLOSED.value: "Case closed",
}
_MONEY_STEPS = {
    "evidence.queued": "Evidence queued",
    "evidence.completed": "Evidence sent to PayPal",
    "evidence.failed": "Evidence not sent",
    "evidence.blocked": "Evidence not sent",
    "offer.queued": "Refund offer queued",
    "offer.completed": "Refund offer made",
    "offer.failed": "Refund offer not confirmed",
    "offer.lowered": "Refund offer lowered",
}


class ProtectionCheck(BaseModel):
    id: Literal["verdict", "address", "delivered", "tracking", "replied"]
    state: CheckState
    detail: str


class Protection(BaseModel):
    status: str | None  # PayPal's verdict word for word: ELIGIBLE | PARTIALLY_ELIGIBLE | ...
    categories: list[str]  # the dispute categories PayPal says it covers
    checks: list[ProtectionCheck]
    met: int
    total: int


class PayPalFacts(BaseModel):
    """Fields PayPal sent in a dispute webhook, from the latest one that carries each."""

    reason: str | None = None
    status: str | None = None
    stage: str | None = None
    channel: str | None = None
    response_due_at: datetime | None = None
    amount_minor: int | None = None
    currency: str | None = None
    outcome_code: str | None = None
    updated_at: datetime | None = None
    events: int = 0


class EvidenceSent(BaseModel):
    status: str  # queued | completed | simulated | failed | blocked
    request_id: str | None  # the PayPal-Request-Id the outbox job sends
    queued_at: datetime
    sent_at: datetime | None
    evidence_type: str | None
    carrier: str | None
    tracking_number: str | None
    notes: list[str]


class OfferMade(BaseModel):
    status: str  # queued | completed | simulated | failed | unconfirmed
    request_id: str | None
    amount_minor: int
    currency: str
    queued_at: datetime
    sent_at: datetime | None


class Outcome(BaseModel):
    code: str | None
    result: Result
    at: datetime | None


class DeskEvent(BaseModel):
    at: datetime
    kind: Literal["paypal", "doorstep", "agent", "money", "receipt"]
    title: str
    detail: str = ""


class DeskRow(BaseModel):
    dispute_id: str
    paypal_dispute_id: str
    simulated: bool
    order_id: str
    order_ref: str
    item: str
    case_id: str | None
    case_state: str | None
    status: str  # open | resolved
    stage: Stage
    opened_at: datetime
    amount_minor: int
    currency: str
    amount_from: Literal["paypal", "order"]
    claim: str | None  # what the buyer reported: not_received, damaged, ...
    failure_point: str | None
    confidence: str | None
    rule_id: str | None
    rule_version: str | None
    reasons: list[str]  # the evidence the decision used, built in code
    scan_verdict: str | None
    recommended: Literal["evidence", "offer"] | None
    can_send_evidence: bool
    offer_minor: int | None  # the refund the seller can still offer inside the dispute
    protection: Protection
    paypal: PayPalFacts
    evidence: EvidenceSent | None
    offer: OfferMade | None
    outcome: Outcome | None
    receipts: int
    timeline: list[DeskEvent]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _words(code: str | None) -> str:
    return code.replace("_", " ").lower() if code else ""


def _webhooks_by_dispute(s: Session, ids: set[str]) -> dict[str, list[WebhookInbox]]:
    """The stored dispute webhooks for these PayPal dispute ids, oldest first."""
    found: dict[str, list[WebhookInbox]] = defaultdict(list)
    if not ids:
        return found
    rows = s.scalars(
        select(WebhookInbox)
        .where(WebhookInbox.source == "paypal", WebhookInbox.event_type.in_(DISPUTE_EVENTS))
        .order_by(WebhookInbox.received_at, WebhookInbox.id)
    )
    for row in rows:
        dispute_id = str(_dict(_dict(row.payload).get("resource")).get("dispute_id") or "")
        if dispute_id in ids:
            found[dispute_id].append(row)
    return found


def paypal_facts(hooks: list[WebhookInbox], read: AuditLog | None = None) -> PayPalFacts:
    """DS-04: each field from the latest webhook that carries it; a field never sent stays None.
    DI-07: `read` is Doorstep's last read of the dispute from PayPal, taken in time order with the
    webhooks, so a server PayPal cannot reach still shows the reason and the reply-by date."""
    facts = PayPalFacts(events=len(hooks))
    sources = [(row.received_at, _dict(_dict(row.payload).get("resource"))) for row in hooks]
    if read is not None:
        sources.append((read.at, _dict(read.after)))
    for at, res in sorted(sources, key=lambda source: source[0]):  # later ones overwrite
        facts.updated_at = at
        facts.reason = _text(res.get("reason")) or facts.reason
        facts.status = _text(res.get("status")) or facts.status
        facts.stage = _text(res.get("dispute_life_cycle_stage")) or facts.stage
        facts.channel = _text(res.get("dispute_channel")) or facts.channel
        facts.response_due_at = _parse_time(res.get("seller_response_due_date")) or (
            facts.response_due_at
        )
        facts.outcome_code = (
            _text(_dict(res.get("dispute_outcome")).get("outcome_code")) or facts.outcome_code
        )
        amount = _dict(res.get("dispute_amount"))
        if amount:
            try:
                value, code = str(amount.get("value")), str(amount.get("currency_code"))
                money = Money.from_paypal(value, code)
            except (ValueError, ValidationError):
                continue
            facts.amount_minor, facts.currency = money.amount_minor, money.currency.value
    return facts


def outcome_result(code: str | None) -> Result:
    if code in _WON:
        return "won"
    if code in _LOST:
        return "lost"
    return "other"


def _address(raw: Any) -> Address | None:
    try:
        return Address.model_validate(raw)
    except ValidationError:
        return None


def _latest_money(actions: list[MoneyAction], kind: MoneyActionKind) -> MoneyAction | None:
    found = [m for m in actions if m.kind == kind.value]
    return max(found, key=lambda m: m.created_at) if found else None


def _live_offer(m: MoneyAction | None) -> bool:
    if m is None:
        return False
    live = (MoneyActionStatus.QUEUED.value, MoneyActionStatus.COMPLETED.value)
    return m.status in live or m.paypal_status in OFFER_MAYBE_MADE


def _evidence_live(m: MoneyAction | None) -> bool:
    live = (MoneyActionStatus.QUEUED.value, MoneyActionStatus.COMPLETED.value)
    return m is not None and m.status in live


class _Desk:
    """Builds the rows for one seller in a handful of queries."""

    def __init__(self, s: Session, rulebook: RuleBook, orders: dict[str, Order]) -> None:
        self.s = s
        self.rulebook = rulebook
        self.orders = orders

    def rows(self) -> list[DeskRow]:
        s = self.s
        disputes = list(
            s.scalars(
                select(Dispute)
                .where(Dispute.order_id.in_(list(self.orders)))
                .order_by(Dispute.opened_at.desc(), Dispute.id)
            )
        )
        if not disputes:
            return []
        order_ids = list({d.order_id for d in disputes})
        self.reports = latest_reports(s, order_ids)
        self.hooks = _webhooks_by_dispute(s, {d.paypal_dispute_id for d in disputes})
        self.reads: dict[str, AuditLog] = {}
        for row in s.scalars(  # oldest first, so each dispute keeps its latest read
            select(AuditLog)
            .where(
                AuditLog.entity_type == "dispute",
                AuditLog.action == READ_ACTION,
                AuditLog.entity_id.in_([d.id for d in disputes]),
            )
            .order_by(AuditLog.at, AuditLog.id)
        ):
            self.reads[row.entity_id] = row
        self.money: dict[str, list[MoneyAction]] = defaultdict(list)
        for m in s.scalars(
            select(MoneyAction)
            .where(MoneyAction.order_id.in_(order_ids), MoneyAction.external.is_(False))
            .order_by(MoneyAction.created_at, MoneyAction.id)
        ):
            self.money[m.order_id].append(m)
        return [self._row(d) for d in disputes]

    def _case(self, order_id: str) -> Case | None:
        return self.s.scalars(
            select(Case).where(Case.order_id == order_id).order_by(Case.opened_at.desc())
        ).first()

    def _row(self, d: Dispute) -> DeskRow:
        s = self.s
        order = self.orders[d.order_id]
        case = self._case(order.id)
        decision = latest_decision(s, case.id) if case else None
        actions = self.money.get(order.id, [])
        evidence_ma = _latest_money(actions, MoneyActionKind.EVIDENCE)
        offer_ma = _latest_money(actions, MoneyActionKind.OFFER)
        hooks = self.hooks.get(d.paypal_dispute_id, [])
        facts = paypal_facts(hooks, self.reads.get(d.id))
        stage: Stage = "needs_reply"
        if d.status == "resolved":
            stage = "resolved"
        elif _live_offer(offer_ma):
            stage = "offer"
        elif _evidence_live(evidence_ma):
            stage = "evidence"
        in_dispute = case is not None and case.state == CaseState.DISPUTE_EVIDENCE.value
        case_actions = [m for m in actions if case is not None and m.case_id == case.id]
        can_send = in_dispute and not any(
            m.kind == MoneyActionKind.EVIDENCE.value for m in case_actions
        )
        offer_minor = None
        if (
            in_dispute
            and case is not None
            and decision is not None
            and not any(m.kind == MoneyActionKind.OFFER.value for m in case_actions)
            and (amount := offer_amount(s, case)) is not None
        ):
            offer_minor = amount.amount_minor
        audit = _dict(decision.signals.get("scan_audit")) if decision else {}
        scan_verdict = _text(audit.get("verdict"))
        recommended: Literal["evidence", "offer"] | None = None
        if stage == "needs_reply" and in_dispute:
            contradicted = scan_verdict == "contradicted"
            recommended = "offer" if contradicted and offer_minor is not None else "evidence"
        outcome = self._outcome(d, case, facts, hooks)
        receipts = (
            list(s.scalars(select(Receipt).where(Receipt.case_id == case.id).order_by(Receipt.seq)))
            if case
            else []
        )
        return DeskRow(
            dispute_id=d.id,
            paypal_dispute_id=d.paypal_dispute_id,
            simulated=is_simulated(d.paypal_dispute_id),
            order_id=order.id,
            order_ref=order.id[:8].upper(),
            item=order.item_description,
            case_id=case.id if case else None,
            case_state=case.state if case else None,
            status=d.status,
            stage=stage,
            opened_at=d.opened_at,
            amount_minor=facts.amount_minor
            if facts.amount_minor is not None
            else order.amount_minor,
            currency=facts.currency or order.currency,
            amount_from="paypal" if facts.amount_minor is not None else "order",
            claim=case.reason if case else None,
            failure_point=decision.failure_point if decision else None,
            confidence=decision.confidence if decision else None,
            rule_id=decision.rule_id if decision else None,
            rule_version=decision.rule_version if decision else None,
            reasons=list(decision.reasons) if decision else [],
            scan_verdict=scan_verdict,
            recommended=recommended,
            can_send_evidence=can_send,
            offer_minor=offer_minor,
            protection=self._protection(order, evidence_ma, offer_ma),
            paypal=facts,
            evidence=self._evidence(evidence_ma),
            offer=self._offer(offer_ma),
            outcome=outcome,
            receipts=len(receipts),
            timeline=self._timeline(d, order, case, actions, hooks, receipts),
        )

    def _outcome(
        self, d: Dispute, case: Case | None, facts: PayPalFacts, hooks: list[WebhookInbox]
    ) -> Outcome | None:
        if d.status != "resolved":
            return None
        code = facts.outcome_code
        at = next(
            (h.received_at for h in reversed(hooks) if h.event_type.endswith("RESOLVED")), None
        )
        if case is not None:
            row = self.s.scalars(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case.id,
                    AuditLog.action.like(f"{_OUTCOME_PREFIX}%"),
                )
                .order_by(AuditLog.at.desc())
            ).first()
            if row is not None:
                code = code or row.action.removeprefix(_OUTCOME_PREFIX)
                at = at or row.at
        if code == "no outcome code":
            code = None
        return Outcome(code=code, result=outcome_result(code), at=at)

    def _protection(
        self, order: Order, evidence_ma: MoneyAction | None, offer_ma: MoneyAction | None
    ) -> Protection:
        s = self.s
        report = self.reports.get(order.id) or {}
        status = _text(report.get("status"))
        categories = [str(c) for c in report.get("dispute_categories") or []]
        checks: list[ProtectionCheck] = []
        verdict: CheckState = _VERDICT.get(status, "partial") if status else "missing"
        checks.append(
            ProtectionCheck(
                id="verdict",
                state=verdict,
                detail=_words(status).capitalize() if status else "Not reported",
            )
        )
        shipment = s.scalars(
            select(Shipment).where(Shipment.order_id == order.id).order_by(Shipment.created_at)
        ).first()
        if shipment is None:
            checks.append(ProtectionCheck(id="address", state="missing", detail="Not shipped"))
        else:
            ordered, sent = _address(order.shipping_address), _address(shipment.delivery_address)
            if ordered is None or sent is None:
                checks.append(ProtectionCheck(id="address", state="missing", detail="No address"))
            elif self.rulebook.seller_protection.is_redirected(ordered, sent):
                checks.append(ProtectionCheck(id="address", state="fail", detail="Changed"))
            else:
                checks.append(ProtectionCheck(id="address", state="pass", detail="Same address"))
        delivered = (
            s.scalars(
                select(TrackingEvent)
                .where(
                    TrackingEvent.shipment_id == shipment.id, TrackingEvent.status == "DELIVERED"
                )
                .order_by(TrackingEvent.occurred_at)
            ).first()
            if shipment
            else None
        )
        checks.append(
            ProtectionCheck(
                id="delivered",
                state="pass" if delivered else "missing",
                detail=f"{shipment.carrier.replace('_', ' ').upper()} scan"
                if delivered and shipment
                else "No delivered scan",
            )
        )
        checks.append(self._tracking(order, shipment))
        checks.append(self._replied(evidence_ma, offer_ma))
        met = sum(1 for c in checks if c.state == "pass")
        return Protection(
            status=status, categories=categories, checks=checks, met=met, total=len(checks)
        )

    def _tracking(self, order: Order, shipment: Shipment | None) -> ProtectionCheck:
        if shipment is None:
            return ProtectionCheck(id="tracking", state="missing", detail="Not shipped")
        if is_simulated(order.capture_id):
            return ProtectionCheck(id="tracking", state="missing", detail="Demo order")
        key = f"{ADD_TRACKING}:{shipment.id}"
        jobs = list(
            self.s.scalars(
                select(Job).where(or_(Job.dedupe_key == key, Job.dedupe_key == f"{key}:retry"))
            )
        )
        if any(j.status == "done" for j in jobs):
            return ProtectionCheck(id="tracking", state="pass", detail="Added")
        if any(j.status == "queued" for j in jobs):
            return ProtectionCheck(id="tracking", state="partial", detail="Queued")
        if jobs:
            return ProtectionCheck(id="tracking", state="fail", detail="Failed")
        return ProtectionCheck(id="tracking", state="missing", detail="Not added")

    @staticmethod
    def _replied(evidence_ma: MoneyAction | None, offer_ma: MoneyAction | None) -> ProtectionCheck:
        for m, what in ((evidence_ma, "Evidence"), (offer_ma, "Offer")):
            if m is not None and money_status(m) in _SENT:
                sent = "sent" if what == "Evidence" else "made"
                return ProtectionCheck(id="replied", state="pass", detail=f"{what} {sent}")
        if any(
            m is not None and m.status == MoneyActionStatus.QUEUED.value
            for m in (
                evidence_ma,
                offer_ma,
            )
        ):
            return ProtectionCheck(id="replied", state="partial", detail="On its way")
        if evidence_ma is not None or offer_ma is not None:
            return ProtectionCheck(id="replied", state="fail", detail="Not sent")
        return ProtectionCheck(id="replied", state="missing", detail="No reply yet")

    def _evidence(self, m: MoneyAction | None) -> EvidenceSent | None:
        if m is None:
            return None
        job = self.s.scalars(select(Job).where(Job.dedupe_key == f"evidence:{m.id}")).first()
        sent = _dict(_dict(job.payload).get("evidence")) if job else {}
        notes = _text(sent.get("notes"))
        if notes is None:  # the audit keeps the notes too
            row = self.s.scalars(
                select(AuditLog).where(
                    AuditLog.entity_type == "money_action",
                    AuditLog.entity_id == m.id,
                    AuditLog.action == "evidence.queued",
                )
            ).first()
            notes = _text(_dict(row.after).get("notes")) if row else None
        status = money_status(m)
        return EvidenceSent(
            status=status,
            request_id=m.request_id,
            queued_at=m.created_at,
            sent_at=m.completed_at if status in _SENT else None,
            evidence_type=_text(sent.get("evidence_type")),
            carrier=_text(sent.get("carrier")) or _text(sent.get("carrier_other")),
            tracking_number=_text(sent.get("tracking_number")),
            notes=[line for line in (notes or "").splitlines() if line.strip()],
        )

    @staticmethod
    def _offer(m: MoneyAction | None) -> OfferMade | None:
        if m is None:
            return None
        status = money_status(m)
        return OfferMade(
            status=status,
            request_id=m.request_id,
            amount_minor=m.amount_minor,
            currency=m.currency,
            queued_at=m.created_at,
            sent_at=m.completed_at if status in _SENT else None,
        )

    def _timeline(
        self,
        d: Dispute,
        order: Order,
        case: Case | None,
        actions: list[MoneyAction],
        hooks: list[WebhookInbox],
        receipts: list[Receipt],
    ) -> list[DeskEvent]:
        """DS-06: the dispute's story from the dispute row, PayPal's webhooks, the audit and the
        signed receipts. Carrier scans stay on the order's own timeline."""
        events: list[DeskEvent] = []
        if not any(h.event_type.endswith("CREATED") for h in hooks):
            events.append(
                DeskEvent(
                    at=d.opened_at,
                    kind="paypal",
                    title="Dispute opened",
                    detail="Simulated" if is_simulated(d.paypal_dispute_id) else "",
                )
            )
        for h in hooks:
            res = _dict(_dict(h.payload).get("resource"))
            verb = h.event_type.rsplit(".", 1)[-1].lower()
            detail = _words(_text(res.get("status")))
            if verb == "created":
                detail = _words(_text(res.get("reason"))) or detail
            if verb == "resolved":
                code = _text(_dict(res.get("dispute_outcome")).get("outcome_code"))
                detail = _words(code) or detail
            title = {"created": "Dispute opened", "resolved": "PayPal resolved it"}.get(
                verb, "PayPal updated it"
            )
            events.append(DeskEvent(at=h.received_at, kind="paypal", title=title, detail=detail))
        replies = (MoneyActionKind.EVIDENCE.value, MoneyActionKind.OFFER.value)
        ma_ids = [m.id for m in actions if m.kind in replies]
        entity = (AuditLog.entity_type == "order") & (AuditLog.entity_id == order.id)
        if case is not None:
            entity = entity | ((AuditLog.entity_type == "case") & (AuditLog.entity_id == case.id))
        if ma_ids:
            entity = entity | (
                (AuditLog.entity_type == "money_action") & AuditLog.entity_id.in_(ma_ids)
            )
        for row in self.s.scalars(select(AuditLog).where(entity)):
            event = self._audit_event(row)
            if event is not None:
                events.append(event)
        for r in receipts:
            if r.kind in _EVIDENCE_RECEIPTS:
                events.append(
                    DeskEvent(
                        at=r.created_at,
                        kind="receipt",
                        title=f"Signed receipt #{r.seq}",
                        detail=r.kind.replace("-", " "),
                    )
                )
        events.sort(key=lambda e: e.at)
        return events

    @staticmethod
    def _audit_event(row: AuditLog) -> DeskEvent | None:
        after = _dict(row.after)
        if row.entity_type == "order":
            if row.action != PROTECTION_ACTION:
                return None
            return DeskEvent(
                at=row.at,
                kind="paypal",
                title="Seller Protection verdict",
                detail=_words(_text(after.get("status"))),
            )
        if row.entity_type == "money_action":
            title = _MONEY_STEPS.get(row.action)
            if title is None:
                return None
            if (
                row.action == "evidence.completed"
                and after.get("paypal_status") == SIMULATED_STATUS
            ):
                title = "Evidence simulated"
            return DeskEvent(
                at=row.at,
                kind="money",
                title=title,
                detail=str(after.get("request_id") or ""),
            )
        agent = _text(after.get("agent"))
        if agent is not None:
            title = _DISPUTE_AGENT_STEPS.get(agent)
            if title is None:
                return None
            return DeskEvent(
                at=row.at, kind="agent", title=title, detail=str(after.get("summary") or "")
            )
        if row.action.startswith(_OUTCOME_PREFIX):
            code = row.action.removeprefix(_OUTCOME_PREFIX)
            return DeskEvent(at=row.at, kind="doorstep", title="Case closed", detail=_words(code))
        title = _CASE_STEPS.get(str(after.get("state") or ""))
        if title is None:
            return None
        return DeskEvent(at=row.at, kind="doorstep", title=title)


def build_disputes_desk_router(deps: AppDeps, services: Services, rulebook: RuleBook) -> APIRouter:
    router = APIRouter()

    @router.get("/api/seller/{token}/disputes/desk", response_model=list[DeskRow])
    def desk(token: str, s: Db) -> list[DeskRow]:
        """DS-02: every PayPal dispute on the seller's orders, with its full story."""
        seller = seller_from_link(s, services, token)
        orders = {o.id: o for o in s.scalars(select(Order).where(Order.seller_id == seller.id))}
        if not orders:
            return []
        return _Desk(s, rulebook, orders).rows()

    return router
