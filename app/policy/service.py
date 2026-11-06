"""Money decisions (SPEC section 7). The ONLY path to refunds (hard rule 1).

Order of checks (PO-13): open dispute -> hold conditions -> no rule set -> liability ->
daily cap -> auto-approve limit -> pre-refund re-check (at execution).

Safety invariants this module keeps (PO-06, PO-07, PO-12):
- a refund amount is never more than what is still refundable, counting queued, completed and
  external refunds, and is re-checked against PayPal's live capture state just before sending;
- one money action per decision and kind (unique constraint), approvals are idempotent;
- every refund is sent with the money action's own PayPal-Request-Id, on every retry;
- nothing here ever denies a buyer: anything uncertain goes to a human (HOLD_FOR_REVIEW) or the
  seller (AWAITING_SELLER), with reasons.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from typing import Any, NamedTuple, cast

from pydantic import BaseModel
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cases.engine import Confidence, EngineResult, FailurePoint
from app.clock import Clock
from app.money import Currency, CurrencyMismatch, Money
from app.orders.enums import (
    ApproverKind,
    BuyerType,
    CaseState,
    Fix,
    MoneyActionKind,
    MoneyActionStatus,
    ProofKind,
)
from app.orders.models import (
    Approval,
    AuditLog,
    Case,
    Decision,
    Dispute,
    MoneyAction,
    Order,
    Seller,
    SellerRule,
)
from app.orders.state import (
    CASE_TRANSITIONS,
    FINISHED_CASE_STATES,
    IllegalTransition,
    transition_case,
)
from app.paypal_client.idempotency import request_id_for
from app.paypal_client.types import RefundInfo, RefundResult
from app.policy.autopilot import matching_rule
from app.policy.fingerprint import evidence_fingerprint
from app.policy.ports import RefundGateway
from app.policy.reasons import Reason, message
from app.policy.rules import RuleBook, effective_limits
from app.worker.models import Job
from app.worker.runner import PermanentError, enqueue

log = logging.getLogger("doorstep.policy")

REFUND_JOB = "paypal.refund"  # payload: {"money_action_id": ...}

# Refund kinds and the statuses that hold or have moved money.
_REFUND_KINDS = (MoneyActionKind.REFUND.value, MoneyActionKind.PARTIAL_REFUND.value)
_COUNTED = (MoneyActionStatus.QUEUED.value, MoneyActionStatus.COMPLETED.value)
# Capture statuses PayPal reports for a capture that can still be (partly) refunded.
_REFUNDABLE_CAPTURE = frozenset({"COMPLETED", "PARTIALLY_REFUNDED"})


class CaseFacts(BaseModel):
    buyer_country: str
    buyer_type: BuyerType = BuyerType.CONSUMER
    proofs: frozenset[ProofKind] = frozenset()
    claim_amount: Money
    preferred_fix: Fix = Fix.REFUND
    # SA-04: the delivered-scan audit found the carrier's own data contradicts the scan.
    scan_contradicted: bool = False


class Approver(BaseModel):
    kind: ApproverKind
    id: str


class PolicyDecision(BaseModel):
    route: CaseState
    fix: Fix | None
    refund_amount: Money | None
    rule_id: str | None
    rule_version: str | None
    reasons: list[str]
    codes: list[Reason] = []
    money_action_id: str | None = None

    @property
    def code(self) -> Reason:
        """The code that chose the route (the last one)."""
        return self.codes[-1]


class PolicyError(Exception):
    """A request the policy cannot act on (wrong state, missing decision)."""


class PolicyStateError(PolicyError, IllegalTransition):
    """approve/decline asked for in a state that does not allow it (ADR-020): an illegal
    transition request, so callers that tolerate IllegalTransition tolerate this too."""


class MissingRecord(PolicyError, PermanentError):
    """A refund job points at a row that no longer exists. Retrying cannot help (PO-12)."""


# ------------------------------------------------------------------ shared helpers


def _audit(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before: dict[str, Any],
    after: dict[str, Any],
    at: datetime,
) -> None:
    session.add(
        AuditLog(
            actor=actor[:64],  # pragma: no mutate - length cap; actors are short constants
            action=action[:64],  # pragma: no mutate - length cap; actions are short constants
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
            at=at,
        )
    )


def _route_case(session: Session, case: Case, to: CaseState, *, actor: str, reason: str) -> None:
    """Move the case only if that transition is allowed; otherwise leave it (already there,
    or a human owns it). Used on the execution path, which must never crash on a race."""
    current = CaseState(case.state)
    if current != to and to in CASE_TRANSITIONS[current]:
        transition_case(session, case, to, actor=actor, reason=reason)


def _open_dispute(session: Session, order_id: str) -> bool:
    return bool(
        session.scalar(
            select(exists().where(Dispute.order_id == order_id, Dispute.status == "open"))
        )
    )


def _refunded_minor(session: Session, order_id: str, *, statuses: tuple[str, ...]) -> int:
    stmt = select(func.coalesce(func.sum(MoneyAction.amount_minor), 0)).where(
        MoneyAction.order_id == order_id,
        MoneyAction.kind.in_(_REFUND_KINDS),
        MoneyAction.status.in_(statuses),
    )
    return int(session.execute(stmt).scalar_one())


def _order_money(order: Order, minor: int) -> Money:
    return Money(amount_minor=minor, currency=Currency(order.currency))


def _remaining_refundable(session: Session, order: Order) -> Money:
    """Captured amount minus every queued, completed or external refund we know of."""
    used = _refunded_minor(session, order.id, statuses=_COUNTED)
    return _order_money(order, max(order.amount_minor - used, 0))


def _latest_decision(session: Session, case_id: str) -> Decision | None:
    return session.scalars(
        select(Decision)
        .where(Decision.case_id == case_id)
        .order_by(
            Decision.created_at.desc(),
            Decision.id.desc(),  # pragma: no mutate - tie-break only; times are unique per case
        )
    ).first()


def _money_audit_payload(
    decision: Decision, approval: Approval | None, ma: MoneyAction
) -> dict[str, Any]:
    return {
        "money_action_id": ma.id,
        "status": ma.status,
        "amount_minor": ma.amount_minor,
        "currency": ma.currency,
        "request_id": ma.request_id,
        "signals": decision.signals,
        "rule_id": decision.rule_id,
        "rule_version": decision.rule_version,
        "model_outputs": decision.model_outputs,
        "approver": (
            {"kind": approval.approver_kind, "id": approval.approver_id}
            if approval  # pragma: no mutate - every queued refund has an approval (defensive)
            else None
        ),
        "paypal_response": ma.paypal_response or {},
    }


def _approval_for(session: Session, decision_id: str) -> Approval | None:
    return session.scalars(
        select(Approval)
        .where(Approval.decision_id == decision_id)
        .order_by(Approval.at.desc())  # pragma: no mutate - one approval per decision
    ).first()


# ------------------------------------------------------------------ the service


class PolicyService:
    def __init__(self, session: Session, rulebook: RuleBook, clock: Clock) -> None:
        self.session = session
        self.rulebook = rulebook
        self.clock = clock

    # ---- decide ---------------------------------------------------------------

    def decide(
        self,
        case: Case,
        result: EngineResult,
        facts: CaseFacts,
        *,
        model_outputs: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Case must be GATHERING_EVIDENCE. Records a Decision, moves the case to DECIDED and
        then to the route; AUTO_REFUND also records a system approval, a queued MoneyAction and
        a REFUND_JOB."""
        s = self.session
        order = s.get(Order, case.order_id)
        seller = s.get(Seller, case.seller_id)
        if order is None or seller is None:
            raise PolicyError(f"case {case.id}: order or seller missing")
        currency = Currency(order.currency)
        if facts.claim_amount.currency != currency:  # PO-10: never compare across currencies
            raise CurrencyMismatch(f"claim in {facts.claim_amount.currency}, order in {currency}")

        transition_case(s, case, CaseState.DECIDED, actor="policy", reason="decision recorded")
        rule = self.rulebook.select(facts.buyer_country, facts.buyer_type)
        outcome = (
            rule.evaluate(facts.proofs, scan_contradicted=facts.scan_contradicted)
            if rule is not None
            else None
        )
        fix = facts.preferred_fix
        remaining = _remaining_refundable(s, order)
        refund = (
            None
            if fix == Fix.RESHIP
            else _order_money(order, min(facts.claim_amount.amount_minor, remaining.amount_minor))
        )
        notes: list[tuple[Reason, str]] = []
        scan_discounted = rule is not None and not rule.contradicted_scan_counts_as_proof
        if facts.scan_contradicted and scan_discounted:
            notes.append((Reason.SCAN_CONTRADICTED, message(Reason.SCAN_CONTRADICTED)))
        applied: list[SellerRule] = []  # AP-03: the seller rule that lifted the limit, if any
        route = self._route(
            case, order, seller, result, outcome, rule is not None, refund, notes, applied
        )
        codes = [code for code, _ in notes]
        reasons = [*result.reasons, *(text for _, text in notes)]
        liability = outcome.model_dump(mode="json") if outcome else None  # pragma: no mutate

        decision = Decision(
            case_id=case.id,
            failure_point=result.failure_point.value,
            confidence=result.confidence.value,
            route=route.value,
            fix=fix.value,
            refund_amount_minor=refund.amount_minor if refund else None,
            currency=currency.value,
            rule_id=outcome.rule_id if outcome else None,
            rule_version=outcome.rule_version if outcome else None,
            reasons=reasons,
            reason_codes=[code.value for code in codes],
            signals={
                "engine": result.model_dump(mode="json"),  # pragma: no mutate
                "facts": facts.model_dump(mode="json"),
                "liability": liability,
                "remaining_refundable_minor": remaining.amount_minor,
            },
            model_outputs=model_outputs or {},
            evidence_fingerprint=evidence_fingerprint(s, order.id),
            capture_snapshot={
                "capture_id": order.capture_id,
                "status": order.capture_status,
                "refunded_minor": _refunded_minor(
                    s, order.id, statuses=(MoneyActionStatus.COMPLETED.value,)
                ),
            },
            created_at=self._next_decision_time(case.id),
        )
        s.add(decision)
        s.flush()
        transition_case(s, case, route, actor="policy", reason=codes[-1].value)

        ma_id: str | None = None
        if route == CaseState.AUTO_REFUND:  # liable seller, refund under every limit
            approver_id = f"seller-rule:{applied[0].id}" if applied else "policy"
            approval = self._record_approval(
                case, decision, Approver(kind=ApproverKind.SYSTEM, id=approver_id), decision.rule_id
            )
            ma_id = self._queue_refund(order, case, decision, cast(Money, refund), approval).id
        return PolicyDecision(
            route=route,
            fix=fix,
            refund_amount=refund,
            rule_id=decision.rule_id,
            rule_version=decision.rule_version,
            reasons=reasons,
            codes=codes,
            money_action_id=ma_id,
        )

    def _route(
        self,
        case: Case,
        order: Order,
        seller: Seller,
        result: EngineResult,
        outcome: Any,
        has_rule: bool,
        refund: Money | None,
        notes: list[tuple[Reason, str]],
        applied: list[SellerRule],
    ) -> CaseState:
        """PO-13 order of checks. Records (code, sentence) for each step that speaks.

        refund is None exactly when the fix is a reship (no money moves)."""

        def note(code: Reason, **values: object) -> None:
            notes.append((code, message(code, **values)))

        # 1. An open PayPal dispute: evidence goes through the Disputes API (PO-05).
        if _open_dispute(self.session, order.id):
            note(Reason.OPEN_DISPUTE)
            return CaseState.DISPUTE_EVIDENCE
        # 2. Hold conditions: never decided automatically (FE-08, PO-04, hard rule 7).
        if result.contradiction:
            note(Reason.CONTRADICTION)
            return CaseState.HOLD_FOR_REVIEW
        if result.confidence == Confidence.LOW:
            note(Reason.LOW_CONFIDENCE)
            return CaseState.HOLD_FOR_REVIEW
        if self._repeat_claimant(case):
            limits = self.rulebook.money_limits
            note(
                Reason.REPEAT_CLAIMANT,
                threshold=limits.repeat_claims_threshold,
                days=limits.repeat_claims_window_days,
            )
            return CaseState.HOLD_FOR_REVIEW
        if refund is not None and (not order.capture_id or order.capture_status != "COMPLETED"):
            note(Reason.NO_COMPLETED_CAPTURE)
            return CaseState.HOLD_FOR_REVIEW
        # 3. No rule set for this buyer: never guess (RL-03).
        if not has_rule:
            note(Reason.NO_RULE_SET)
            return CaseState.HOLD_FOR_REVIEW
        # 4. Liability. Anything that would blame the buyer goes to the seller (FE-11).
        note(
            Reason.LIABILITY_RULE,
            rule_id=outcome.rule_id,
            rule_version=outcome.rule_version,
            liable_party=outcome.liable_party,
        )
        if outcome.liable_party == "undetermined":
            note(Reason.LIABILITY_UNDETERMINED)
            return CaseState.HOLD_FOR_REVIEW
        if outcome.liable_party != "seller":
            note(Reason.POSSESSION_PROVEN)
            return CaseState.AWAITING_SELLER
        if result.failure_point == FailurePoint.STOLEN_AFTER_DELIVERY or (
            result.failure_point == FailurePoint.BAD_ADDRESS and order.address_override
        ):
            note(Reason.WOULD_BLAME_BUYER)
            return CaseState.AWAITING_SELLER
        if refund is None:
            note(Reason.RESHIP)
            return CaseState.AWAITING_SELLER
        if refund.amount_minor == 0:
            note(Reason.NOTHING_LEFT_TO_REFUND)
            return CaseState.HOLD_FOR_REVIEW
        limits = effective_limits(self.rulebook, seller, refund.currency)
        # 5. Daily cap per seller and currency, UTC day; queued + completed count (PO-08).
        if self._refunded_today(seller.id, refund.currency) + refund > limits.daily_cap:
            note(Reason.DAILY_CAP_REACHED, cap=limits.daily_cap)
            return CaseState.AWAITING_SELLER
        # 6. Auto-approve limit, inclusive (ADR-004), or the seller's own autopilot rule (AP-03).
        if refund > limits.auto_approve:
            seller_rule = matching_rule(
                self.session, self.rulebook, seller.id, result.failure_point, refund
            )
            if seller_rule is None:
                note(Reason.OVER_AUTO_APPROVE_LIMIT, limit=limits.auto_approve)
                return CaseState.AWAITING_SELLER
            applied.append(seller_rule)
            rule_max = Money(amount_minor=seller_rule.max_amount_minor, currency=refund.currency)
            note(Reason.SELLER_RULE, limit=limits.auto_approve, rule_max=rule_max)
            return CaseState.AUTO_REFUND
        note(Reason.AUTO_REFUND, limit=limits.auto_approve)
        return CaseState.AUTO_REFUND

    def _next_decision_time(self, case_id: str) -> datetime:
        """Clock time, nudged forward so decisions on one case stay strictly ordered."""
        now = self.clock.now()
        last = _latest_decision(self.session, case_id)
        if last is not None and last.created_at >= now:
            return last.created_at + timedelta(microseconds=1)  # pragma: no mutate - any step works
        return now

    def _repeat_claimant(self, case: Case) -> bool:
        if not case.payer_id:
            return False
        limits = self.rulebook.money_limits
        since = self.clock.now() - timedelta(days=limits.repeat_claims_window_days)
        others = self.session.execute(
            select(func.count())
            .select_from(Case)
            .where(
                Case.seller_id == case.seller_id,
                Case.payer_id == case.payer_id,
                Case.id != case.id,
                Case.opened_at >= since,
            )
        ).scalar_one()
        return int(others) + 1 >= limits.repeat_claims_threshold

    def _refunded_today(self, seller_id: str, currency: Currency) -> Money:
        start = datetime.combine(self.clock.now().date(), time.min, tzinfo=UTC)  # Clock is UTC
        total = self.session.execute(
            select(func.coalesce(func.sum(MoneyAction.amount_minor), 0)).where(
                MoneyAction.seller_id == seller_id,
                MoneyAction.currency == currency.value,
                MoneyAction.kind.in_(_REFUND_KINDS),
                MoneyAction.status.in_(_COUNTED),
                MoneyAction.external.is_(False),
                MoneyAction.created_at >= start,
                MoneyAction.created_at < start + timedelta(days=1),
            )
        ).scalar_one()
        return Money(amount_minor=int(total), currency=currency)

    # ---- approvals --------------------------------------------------------------

    def _record_approval(
        self, case: Case, decision: Decision, approver: Approver, rule_id: str | None
    ) -> Approval:
        approval = Approval(
            case_id=case.id,
            decision_id=decision.id,
            approver_kind=approver.kind.value,
            approver_id=approver.id,
            rule_id=rule_id,
            at=self.clock.now(),
        )
        self.session.add(approval)
        self.session.flush()
        return approval

    def _queue_refund(
        self, order: Order, case: Case, decision: Decision, amount: Money, approval: Approval
    ) -> MoneyAction:
        """Claim (unique decision+kind) -> money action -> outbox job, in one transaction."""
        s = self.session
        kind = (
            MoneyActionKind.PARTIAL_REFUND
            if amount.amount_minor < order.amount_minor
            else MoneyActionKind.REFUND
        )
        ma = MoneyAction(
            order_id=order.id,
            case_id=case.id,
            decision_id=decision.id,
            seller_id=order.seller_id,
            kind=kind.value,
            amount_minor=amount.amount_minor,
            currency=amount.currency.value,
            status=MoneyActionStatus.QUEUED.value,
            created_at=self.clock.now(),
        )
        with s.begin_nested():  # IntegrityError here = a concurrent approval won (PO-07)
            s.add(ma)
            s.flush()
            ma.request_id = request_id_for("case", case.id, kind.value, ma.id)
            s.flush()
        enqueue(
            s,
            REFUND_JOB,
            {"money_action_id": ma.id},
            clock=self.clock,
            dedupe_key=f"refund:{ma.id}",
        )
        _audit(
            s,
            actor=f"{approval.approver_kind}:{approval.approver_id}",
            action="refund.queued",
            entity_type="money_action",
            entity_id=ma.id,
            before={},
            after=_money_audit_payload(decision, approval, ma),
            at=self.clock.now(),
        )
        return ma

    def _existing_refund(self, decision: Decision) -> MoneyAction | None:
        return self.session.scalars(
            select(MoneyAction).where(
                MoneyAction.decision_id == decision.id, MoneyAction.kind.in_(_REFUND_KINDS)
            )
        ).first()

    def approve(self, case: Case, approver: Approver) -> MoneyAction | None:
        """Seller (AWAITING_SELLER) or reviewer (HOLD_FOR_REVIEW) approves the latest decision.
        Idempotent: approving twice yields one MoneyAction (PO-07)."""
        s = self.session
        if approver.kind == ApproverKind.SYSTEM:
            raise PolicyError("system approvals are made only by decide()")
        s.flush()
        # Serialise concurrent approvals of one case. Only observable on Postgres (the pg PO-07
        # test); SQLite ignores FOR UPDATE.
        s.refresh(case, with_for_update=True)  # pragma: no mutate
        decision = _latest_decision(s, case.id)
        if decision is None:
            raise PolicyStateError(f"case {case.id} has no decision to approve")
        existing = self._existing_refund(decision)
        if existing is not None:
            return existing
        if CaseState(case.state) not in (CaseState.AWAITING_SELLER, CaseState.HOLD_FOR_REVIEW):
            raise PolicyStateError(f"case {case.id} in {case.state} cannot be approved")
        if decision.fix == Fix.RESHIP.value:
            self.settle_with_reship(case, approver)
            return None
        order = s.get(Order, case.order_id)
        assert order is not None
        if not order.capture_id or order.capture_status != "COMPLETED":
            raise PolicyStateError(f"order {order.id} has no completed capture to refund")
        remaining = _remaining_refundable(s, order)
        wanted = cast(int, decision.refund_amount_minor)  # set on every refund decision
        amount = _order_money(order, min(wanted, remaining.amount_minor))
        if amount.amount_minor == 0:
            log.info("case %s approved but nothing is left to refund", case.id)
            return None
        approval = self._record_approval(case, decision, approver, decision.rule_id)
        try:
            return self._queue_refund(order, case, decision, amount, approval)
        except IntegrityError:
            found = self._existing_refund(decision)
            if found is None:  # the claim was rejected for a reason we cannot explain
                raise
            return found

    def decline(self, case: Case, approver: Approver) -> None:
        """Seller declines: AWAITING_SELLER -> CLOSED, no money moves (PO-03)."""
        if CaseState(case.state) != CaseState.AWAITING_SELLER:
            raise PolicyStateError(f"case {case.id} in {case.state} cannot be declined")
        decision = _latest_decision(self.session, case.id)
        if decision is not None and self._existing_refund(decision) is not None:
            raise PolicyStateError(f"case {case.id}: refund already approved, cannot decline")
        transition_case(
            self.session,
            case,
            CaseState.CLOSED,
            actor=f"{approver.kind}:{approver.id}",
            reason="seller declined",
        )

    def settle_with_reship(self, case: Case, approver: Approver) -> None:
        """Reship fix: records the approval and settles with no MoneyAction (PO-11)."""
        decision = _latest_decision(self.session, case.id)
        if decision is None or decision.fix != Fix.RESHIP.value:
            raise PolicyError(f"case {case.id}: latest decision is not a reship")
        self._record_approval(case, decision, approver, decision.rule_id)
        transition_case(
            self.session,
            case,
            CaseState.SETTLED,
            actor=f"{approver.kind}:{approver.id}",
            reason="reship recorded",
        )

    def record_external_refund(
        self, order: Order, amount: Money, *, paypal_refund_id: str
    ) -> MoneyAction:
        """A refund made outside Doorstep (PAYMENT.CAPTURE.REFUNDED) (PO-06).

        Idempotent on the PayPal refund id; a refund Doorstep made itself is recognised by it.
        """
        s = self.session
        if amount.currency != order.currency:
            raise CurrencyMismatch(f"refund in {amount.currency}, order in {order.currency}")
        known = s.scalars(
            select(MoneyAction).where(MoneyAction.paypal_refund_id == paypal_refund_id)
        ).first()
        if known is not None:
            return known
        kind = (
            MoneyActionKind.PARTIAL_REFUND
            if amount.amount_minor < order.amount_minor
            else MoneyActionKind.REFUND
        )
        ma = MoneyAction(
            order_id=order.id,
            seller_id=order.seller_id,
            kind=kind.value,
            amount_minor=amount.amount_minor,
            currency=amount.currency.value,
            status=MoneyActionStatus.COMPLETED.value,
            external=True,
            paypal_refund_id=paypal_refund_id,
            paypal_status="COMPLETED",
            created_at=self.clock.now(),
            completed_at=self.clock.now(),
        )
        s.add(ma)
        s.flush()
        _audit(
            s,
            actor="paypal",
            action="refund.external",
            entity_type="money_action",
            entity_id=ma.id,
            before={},
            after={
                "amount_minor": ma.amount_minor,
                "currency": ma.currency,
                "paypal_refund_id": paypal_refund_id,
            },
            at=self.clock.now(),
        )
        return ma


# ------------------------------------------------------------------ execution


class _Verdict(NamedTuple):
    """Outcome of the pre-refund re-check. code None: the refund may go out (or was recovered)."""

    code: Reason | None
    target: CaseState = CaseState.HOLD_FOR_REVIEW  # used only when code is set
    capture_id: str = ""
    recovered: RefundInfo | None = None
    text: str = ""

    @classmethod
    def block(
        cls, code: Reason, target: CaseState = CaseState.HOLD_FOR_REVIEW, **values: object
    ) -> _Verdict:
        return cls(code, target, text=message(code, **values))


class RefundJobHandler:
    """Executes REFUND_JOB: pre-refund re-check (dispute, capture state, evidence fingerprint),
    then refunds with the MoneyAction's request_id. Retries keep the same key (PO-12)."""

    def __init__(self, gateway: RefundGateway, rulebook: RuleBook, clock: Clock) -> None:
        del rulebook  # the re-check needs no rules: the decision already recorded them (PO-12)
        self.gateway = gateway
        self.clock = clock

    def _load(self, session: Session, job: Job) -> tuple[MoneyAction, Order, Case, Decision]:
        ma_id = str(job.payload.get("money_action_id", ""))  # pragma: no mutate
        ma = session.scalar(select(MoneyAction).where(MoneyAction.id == ma_id).with_for_update())
        if ma is None:
            raise MissingRecord(f"job {job.id}: money action missing")
        order = session.get(Order, ma.order_id)
        case = session.get(Case, ma.case_id) if ma.case_id else None  # pragma: no mutate
        did = ma.decision_id
        decision = session.get(Decision, did) if did else None  # pragma: no mutate
        if order is None or case is None or decision is None:
            raise MissingRecord(f"job {job.id}: order, case or decision missing")
        return ma, order, case, decision

    def _recheck(
        self, session: Session, ma: MoneyAction, order: Order, case: Case, decision: Decision
    ) -> _Verdict:
        """Decide, right before PayPal, whether this refund may go out (PO-12, ADR-016, ADR-022)."""
        state = CaseState(case.state)
        if state in FINISHED_CASE_STATES:  # closed or settled another way: never send money
            return _Verdict.block(Reason.CASE_FINISHED, state=state.value)  # terminal: case stays
        if _open_dispute(session, order.id):
            return _Verdict.block(Reason.DISPUTE_OPENED, CaseState.DISPUTE_EVIDENCE)
        if evidence_fingerprint(session, order.id) != decision.evidence_fingerprint:
            return _Verdict.block(Reason.EVIDENCE_CHANGED)
        if not order.paypal_order_id or not order.capture_id:
            return _Verdict.block(Reason.NO_PAYPAL_CAPTURE)
        live = self.gateway.get_capture_state(order.paypal_order_id, order.capture_id)
        if live.amount.currency != order.currency or live.refunded_total.currency != order.currency:
            return _Verdict.block(Reason.CAPTURE_CURRENCY_MISMATCH)
        known = _refunded_minor(session, order.id, statuses=(MoneyActionStatus.COMPLETED.value,))
        refunded = live.refunded_total.amount_minor
        ours = [r for r in live.refunds if r.custom_id == ma.id]
        if ours:  # an earlier attempt landed but its commit was lost (ADR-022)
            landed = ours[0]
            if (
                len(ours) == 1
                and landed.amount.amount_minor == ma.amount_minor
                and refunded == known + ma.amount_minor
            ):  # checked before the capture status: a full refund leaves it REFUNDED
                return _Verdict(None, recovered=landed)
            return _Verdict.block(Reason.UNKNOWN_REFUND_AT_PAYPAL)
        if live.status not in _REFUNDABLE_CAPTURE:
            return _Verdict.block(Reason.CAPTURE_NOT_REFUNDABLE, status=live.status)
        if refunded != known:  # a refund we cannot identify: never guess (ADR-016)
            return _Verdict.block(Reason.UNKNOWN_REFUND_AT_PAYPAL)
        # Never more than PayPal captured, nor more than Doorstep's order record says.
        if known + ma.amount_minor > min(live.amount.amount_minor, order.amount_minor):
            return _Verdict.block(Reason.WOULD_EXCEED_CAPTURE)
        return _Verdict(None, capture_id=live.capture_id)

    def run(self, session: Session, job: Job) -> None:
        ma, order, case, decision = self._load(session, job)
        if ma.status != MoneyActionStatus.QUEUED.value:
            return  # already completed, failed or blocked: a redelivered job sends nothing
        approval = _approval_for(session, decision.id)
        verdict = self._recheck(session, ma, order, case, decision)
        now = self.clock.now()
        if verdict.code is not None:
            ma.status = MoneyActionStatus.BLOCKED.value
            ma.paypal_response = {"blocked": verdict.text, "blocked_code": verdict.code.value}
            _audit(
                session,
                actor="policy",
                action="refund.blocked",
                entity_type="money_action",
                entity_id=ma.id,
                before={"status": MoneyActionStatus.QUEUED.value},
                after=_money_audit_payload(decision, approval, ma),
                at=now,
            )
            _route_case(session, case, verdict.target, actor="policy", reason=verdict.code.value)
            log.warning("refund %s blocked: %s", ma.id, verdict.text)
            return
        if verdict.recovered is not None:  # ours already landed: record it, never resend
            result = RefundResult(
                refund_id=verdict.recovered.refund_id,
                status=verdict.recovered.status,
                raw={"recovered": True},
            )
        else:
            result = self.gateway.refund_capture(
                verdict.capture_id,
                amount=_order_money(order, ma.amount_minor),
                request_id=cast(str, ma.request_id),  # set when the refund was queued
                custom_id=ma.id,
            )
        ma.status = MoneyActionStatus.COMPLETED.value
        ma.paypal_refund_id = result.refund_id
        ma.paypal_status = result.status
        ma.paypal_response = {
            "refund_id": result.refund_id,
            "status": result.status,
            **dict(result.raw),
        }
        ma.completed_at = now
        _audit(
            session,
            actor="paypal",
            action="refund.completed",
            entity_type="money_action",
            entity_id=ma.id,
            before={"status": MoneyActionStatus.QUEUED.value},
            after=_money_audit_payload(decision, approval, ma),
            at=now,
        )
        _route_case(session, case, CaseState.SETTLED, actor="policy", reason="refund completed")

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        """Retries exhausted or a 4xx: mark failed, never retry with a new key, hold (PO-12)."""
        try:
            ma, _order, case, decision = self._load(session, job)
        except MissingRecord:
            self._fail_dangling(session, job, error)
            return
        if ma.status != MoneyActionStatus.QUEUED.value:
            return
        ma.status = MoneyActionStatus.FAILED.value
        ma.paypal_response = {
            "error": f"{type(error).__name__}: {error}"[:500]  # pragma: no mutate - length cap
        }
        _audit(
            session,
            actor="worker",
            action="refund.failed",
            entity_type="money_action",
            entity_id=ma.id,
            before={"status": MoneyActionStatus.QUEUED.value},
            after=_money_audit_payload(decision, _approval_for(session, decision.id), ma),
            at=self.clock.now(),
        )
        _route_case(
            session, case, CaseState.HOLD_FOR_REVIEW, actor="policy", reason="refund failed"
        )

    def _fail_dangling(self, session: Session, job: Job, error: Exception) -> None:
        """A row behind the job is gone, so no refund is ever sent for it (ADR-021).

        The job only fails while its money action is queued (a completed refund commits with the
        job's done status), so the money action is marked failed without a status check. The case
        is held if it can still be found. A job whose money action itself is gone is an orphan:
        logged at ERROR and listed by app.worker.orphans.orphaned_refund_jobs.
        """
        ma_id = str(job.payload.get("money_action_id", ""))  # pragma: no mutate
        ma = session.get(MoneyAction, ma_id)
        if ma is None:
            log.error("orphaned refund job %s: money action %r does not exist", job.id, ma_id)
            return
        ma.status = MoneyActionStatus.FAILED.value
        ma.paypal_response = {
            "error": f"{type(error).__name__}: {error}"[:500]  # pragma: no mutate - length cap
        }
        log.error("refund job %s failed on a dangling reference: %s", job.id, error)
        case = session.get(Case, ma.case_id or "")  # pragma: no mutate
        if case is not None:
            _route_case(
                session, case, CaseState.HOLD_FOR_REVIEW, actor="policy", reason="record missing"
            )
