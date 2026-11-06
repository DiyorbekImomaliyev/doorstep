"""AP-01..05: seller autopilot rules, learned from the seller's own decisions (the Learn stage).

A seller who keeps approving the same kind of refund should not have to keep tapping. Doorstep
watches only what the seller already decided and offers a standing rule; the seller turns it on.

- `suggest_rules`: computed in code from the seller's approvals; no model is involved (AP-01).
  A precedent is a seller approval of a case that waited for the seller only because the refund
  was over the auto-approve limit. One decline of that kind in the window blocks the suggestion.
- `turn_on` / `dismiss`: only from a current suggestion, so a rule's maximum is always the largest
  refund the seller already approved, capped by the rule file's ceiling (AP-02). Nobody types it.
- `turn_off`: the seller's own active rule, one tap (AP-05). Every change writes an audit row.
- `matching_rule`: what `PolicyService._route` asks at its last step, the auto-approve limit check
  (AP-03/04). Every earlier check still runs first, and a rule can never hold or deny a case.
- `statement`: what the rules refunded in the last 7 days and what carriers owe back (AP-07),
  read from stored records. Automatic spending is only trusted when it is accounted for.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import cast

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases.engine import FailurePoint
from app.money import Currency, Money
from app.orders.enums import (
    ApproverKind,
    CaseState,
    MoneyActionKind,
    MoneyActionStatus,
    RecoveryStatus,
)
from app.orders.models import (
    Approval,
    AuditLog,
    Case,
    Decision,
    MoneyAction,
    Order,
    Recovery,
    Seller,
    SellerRule,
)
from app.policy.reasons import Reason
from app.policy.rules import RuleBook, effective_limits

ON, OFF, DISMISSED = "on", "off", "dismissed"
DECLINED = "transition: seller declined"  # the audit action PolicyService.decline writes
RULE_APPROVER = "seller-rule:"  # the system approval's id prefix when a seller rule refunded
STATEMENT_DAYS = 7


class AutopilotError(Exception):
    """No current suggestion for that kind, or no such active rule for this seller."""


class RuleSuggestion(BaseModel):
    failure_point: FailurePoint
    currency: Currency
    max_amount_minor: int
    approved: int  # seller approvals counted as precedents
    window_days: int
    case_ids: list[str]


Kind = tuple[str, str]  # (failure point, currency)


def _approvals(
    session: Session, seller_id: str, since: datetime
) -> dict[Kind, list[tuple[str, int]]]:
    """Seller approvals in the window of cases that waited only for the auto-approve limit."""
    rows = session.execute(
        select(
            Approval.case_id,
            Decision.failure_point,
            Decision.currency,
            Decision.refund_amount_minor,
            Decision.route,
            Decision.reason_codes,
        )
        # Explicit ON clauses. Each is the only foreign key between the two tables (the
        # decision belongs to the approval's case), so dropping one is an equivalent mutant.
        .join(Decision, Decision.id == Approval.decision_id)  # pragma: no mutate
        .join(Case, Case.id == Approval.case_id)  # pragma: no mutate
        .where(
            Case.seller_id == seller_id,
            Approval.approver_kind == ApproverKind.SELLER.value,
            Approval.at >= since,
        )
        .order_by(Approval.at, Approval.case_id)
    ).all()
    out: dict[Kind, list[tuple[str, int]]] = {}
    for case_id, fp, currency, amount, route, codes in rows:
        over_limit_only = (
            route == CaseState.AWAITING_SELLER.value
            and bool(codes)
            and codes[-1] == Reason.OVER_AUTO_APPROVE_LIMIT.value
        )
        if over_limit_only and amount and fp and currency:
            out.setdefault((fp, currency), []).append((case_id, amount))
    return out


def _declined(session: Session, seller_id: str, since: datetime) -> set[Kind]:
    """Kinds (failure point, currency) the seller declined in the window."""
    declined = (
        select(AuditLog.entity_id)
        .join(Case, Case.id == AuditLog.entity_id)
        .where(
            Case.seller_id == seller_id,
            # Only case transitions write DECLINED, and the join needs a case id: equivalent.
            AuditLog.entity_type == "case",  # pragma: no mutate
            AuditLog.action == DECLINED,
            AuditLog.at >= since,
        )
    )
    rows = session.execute(
        select(Decision.failure_point, Decision.currency).where(
            Decision.case_id.in_(declined),
            # A (None, currency) kind can never match a precedent kind, which needs both: these
            # two filters keep the set clean and change no suggestion (equivalent mutants).
            Decision.failure_point.is_not(None),  # pragma: no mutate
            Decision.currency.is_not(None),  # pragma: no mutate
        )
    ).all()
    return {(cast(str, fp), cast(str, cur)) for fp, cur in rows}


def suggest_rules(
    session: Session, book: RuleBook, seller: Seller, *, now: datetime
) -> list[RuleSuggestion]:
    cfg = book.money_limits.seller_rules
    if cfg is None:
        return []
    since = now - timedelta(days=cfg.window_days)
    allowed = {fp.value for fp in cfg.failure_points}
    taken = {
        (r.failure_point, r.currency)
        for r in session.scalars(select(SellerRule).where(SellerRule.seller_id == seller.id))
    }
    declined = _declined(session, seller.id, since)
    out: list[RuleSuggestion] = []
    for kind, cases in sorted(_approvals(session, seller.id, since).items()):
        fp, cur = kind
        if (
            fp not in allowed
            or kind in taken
            or kind in declined
            or len(cases) < cfg.min_precedents
        ):
            continue
        currency = Currency(cur)
        top = min(max(amount for _, amount in cases), cfg.ceiling(currency).amount_minor)
        if top <= effective_limits(book, seller, currency).auto_approve.amount_minor:
            continue  # the ordinary limit already covers it: nothing to automate
        out.append(
            RuleSuggestion(
                failure_point=FailurePoint(fp),
                currency=currency,
                max_amount_minor=top,
                approved=len(cases),
                window_days=cfg.window_days,
                case_ids=[case_id for case_id, _ in cases],
            )
        )
    return out


def _current(
    session: Session, book: RuleBook, seller: Seller, fp: FailurePoint, currency: str, now: datetime
) -> RuleSuggestion:
    for s in suggest_rules(session, book, seller, now=now):
        if s.failure_point == fp and s.currency == currency:
            return s
    raise AutopilotError("No current suggestion for this kind of case.")


def _audit(
    session: Session,
    rule: SellerRule,
    action: str,
    actor: str,
    before: dict[str, str],
    now: datetime,
) -> None:
    session.add(
        AuditLog(
            actor=actor[:64],
            action=action,
            entity_type="seller_rule",
            entity_id=rule.id,
            before=before,
            after={
                "status": rule.status,
                "failure_point": rule.failure_point,
                "currency": rule.currency,
                "max_amount_minor": rule.max_amount_minor,
                "precedents": list(rule.precedents),
            },
            at=now,
        )
    )
    session.flush()


def _create(
    session: Session,
    book: RuleBook,
    seller: Seller,
    fp: FailurePoint,
    currency: str,
    status: str,
    *,
    now: datetime,
    actor: str,
) -> SellerRule:
    s = _current(session, book, seller, fp, currency, now)
    rule = SellerRule(
        seller_id=seller.id,
        failure_point=s.failure_point.value,
        currency=s.currency.value,
        max_amount_minor=s.max_amount_minor,
        status=status,
        precedents=s.case_ids,
        updated_at=now,
    )
    session.add(rule)
    session.flush()
    _audit(session, rule, f"seller_rule.{status}", actor, {}, now)
    return rule


def turn_on(
    session: Session,
    book: RuleBook,
    seller: Seller,
    fp: FailurePoint,
    currency: str,
    *,
    now: datetime,
    actor: str,
) -> SellerRule:
    return _create(session, book, seller, fp, currency, ON, now=now, actor=actor)


def dismiss(
    session: Session,
    book: RuleBook,
    seller: Seller,
    fp: FailurePoint,
    currency: str,
    *,
    now: datetime,
    actor: str,
) -> SellerRule:
    return _create(session, book, seller, fp, currency, DISMISSED, now=now, actor=actor)


def turn_off(
    session: Session, seller: Seller, rule_id: str, *, now: datetime, actor: str
) -> SellerRule:
    rule = session.get(SellerRule, rule_id)
    if rule is None or rule.seller_id != seller.id or rule.status != ON:
        raise AutopilotError("No such active rule.")
    rule.status = OFF
    rule.updated_at = now
    _audit(session, rule, "seller_rule.off", actor, {"status": ON}, now)
    return rule


def active_rules(session: Session, seller_id: str) -> Sequence[SellerRule]:
    return session.scalars(
        select(SellerRule)
        .where(SellerRule.seller_id == seller_id, SellerRule.status == ON)
        # SQLite's unique index (seller, kind, currency) already returns this order, so the unit
        # suite cannot see the currency sort; tests/integration/test_pg_ap_rule_order.py does.
        .order_by(SellerRule.failure_point, SellerRule.currency)  # pragma: no mutate
    ).all()


def matching_rule(
    session: Session, book: RuleBook, seller_id: str, fp: FailurePoint, refund: Money
) -> SellerRule | None:
    """The seller's active rule that covers this refund, within its maximum and the ceiling."""
    cfg = book.money_limits.seller_rules
    if cfg is None or fp not in cfg.failure_points:
        return None
    rule = session.scalars(
        select(SellerRule).where(
            SellerRule.seller_id == seller_id,
            SellerRule.failure_point == fp.value,
            SellerRule.currency == refund.currency.value,
            SellerRule.status == ON,
        )
    ).first()
    if rule is None:
        return None
    cap = min(rule.max_amount_minor, cfg.ceiling(refund.currency).amount_minor)
    return rule if refund.amount_minor <= cap else None


# ---------------------------------------------------------------- AP-07 statement


class StatementCase(BaseModel):
    case_id: str
    order_id: str
    order_ref: str
    item: str
    amount_minor: int
    currency: str
    refunded_at: datetime


class StatementTotal(BaseModel):
    currency: str
    refunds: int = 0
    refunded_minor: int = 0
    claimable_minor: int = 0  # carrier claims prepared or submitted for those cases
    recovered_minor: int = 0  # what carriers paid back
    ready_to_send: int = 0  # claims prepared, not yet submitted


class Statement(BaseModel):
    days: int
    totals: list[StatementTotal]
    cases: list[StatementCase]


def statement(
    session: Session, seller_id: str, *, now: datetime, days: int = STATEMENT_DAYS
) -> Statement:
    """Completed refunds a seller rule approved in the window, with their carrier claims."""
    by_rule = select(Approval.decision_id).where(
        Approval.approver_kind == ApproverKind.SYSTEM.value,
        Approval.approver_id.startswith(RULE_APPROVER),
    )
    rows = session.execute(
        select(MoneyAction, Order, Recovery)
        .join(Order, Order.id == MoneyAction.order_id)
        # An order has at most one case (RESOLVED is terminal), so the join SQLAlchemy infers
        # without this ON clause (recovery.order_id = order.id) is the same: equivalent mutant.
        .outerjoin(Recovery, Recovery.case_id == MoneyAction.case_id)  # pragma: no mutate
        .where(
            MoneyAction.seller_id == seller_id,
            MoneyAction.kind.in_(
                (MoneyActionKind.REFUND.value, MoneyActionKind.PARTIAL_REFUND.value)
            ),
            MoneyAction.status == MoneyActionStatus.COMPLETED.value,
            MoneyAction.completed_at >= now - timedelta(days=days),
            MoneyAction.decision_id.in_(by_rule),
        )
        .order_by(MoneyAction.completed_at.desc(), MoneyAction.id)
    ).all()
    totals: dict[str, StatementTotal] = {}
    cases: list[StatementCase] = []
    for ma, order, rec in rows:
        t = totals.setdefault(ma.currency, StatementTotal(currency=ma.currency))
        t.refunds += 1
        t.refunded_minor += ma.amount_minor
        if rec is not None:
            if rec.status in (RecoveryStatus.PREPARED.value, RecoveryStatus.SUBMITTED.value):
                t.claimable_minor += rec.claim_minor
            if rec.status == RecoveryStatus.PREPARED.value:
                t.ready_to_send += 1
            if rec.status == RecoveryStatus.PAID.value:
                t.recovered_minor += rec.recovered_minor or 0
        cases.append(
            StatementCase(
                case_id=ma.case_id or "",
                order_id=order.id,
                order_ref=order.id[:8].upper(),
                item=order.item_description,
                amount_minor=ma.amount_minor,
                currency=ma.currency,
                refunded_at=ma.completed_at or now,
            )
        )
    return Statement(days=days, totals=[totals[c] for c in sorted(totals)], cases=cases)
