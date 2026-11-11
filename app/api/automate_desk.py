"""AP-10..AP-13, PL-10..PL-12: what the Autopilot and Policy pages read (seller link only).

Read only: no write, no PayPal call, no model call. Every number comes from rules/*.yaml, the
seller's own settings or stored records.

- Cards (AP-10): one per kind of case a rule may cover (`seller_rules` in money_limits_v1), with
  its status, the rule's own maximum, the file's ceiling, the auto-approve limit and daily cap
  (`effective_limits`), what it refunded and when it last ran. A kind Doorstep is still learning
  shows the suggestion it would make, from `suggest_rules` itself, before it has enough approvals.
- Runs (AP-11): every refund a seller rule approved, from approvals and money actions.
- Replay (AP-12): the shop's decided cases in the rule window, run again at the one step a rule
  changes. A case the policy stopped only at the auto-approve limit is put to `matching_rule`
  again, with the rule being replayed; the daily cap, checked before it, is re-added with the
  refunds the rule would have made earlier that UTC day. Every other case keeps its route: a rule
  never holds, decides or lifts any other check.
- Policy (PL-10): the seller's written policy and the rule files its check reads.

Rule 9: items, order references and amounts only; no buyer name, address, email or payer id.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Literal, cast

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.autopilot import seller_from_link
from app.api.deps import AppDeps, Services
from app.api.journey import Db
from app.api.policy_check import HOME_CURRENCY
from app.api.views import money_status
from app.cases.engine import FailurePoint
from app.money import Currency, Money
from app.orders.enums import ApproverKind
from app.orders.models import Approval, Case, Decision, MoneyAction, Order, Seller, SellerRule
from app.policy.autopilot import (
    ON,
    RULE_APPROVER,
    RuleSuggestion,
    Statement,
    matching_rule,
    statement,
    suggest_rules,
)
from app.policy.reasons import Reason
from app.policy.rules import MoneyLimits, RuleBook, RuleSet, effective_limits
from app.policy.service import _COUNTED, _REFUND_KINDS

CardStatus = Literal["on", "off", "dismissed", "offered", "learning", "idle"]
Outcome = Literal["rule", "auto", "you"]

# Where the policy's own routing stopped, by the last reason code it wrote (PO-13 order).
_LIMIT_STEP = {Reason.OVER_AUTO_APPROVE_LIMIT.value, Reason.SELLER_RULE.value}
_PAST_CAP = {*_LIMIT_STEP, Reason.AUTO_REFUND.value}
_LEFT = {
    Reason.OPEN_DISPUTE.value: "dispute",
    Reason.POSSESSION_PROVEN.value: "seller_decides",
    Reason.WOULD_BLAME_BUYER.value: "seller_decides",
    Reason.RESHIP.value: "reship",
    Reason.DAILY_CAP_REACHED.value: "daily_cap",
}  # every other stop is a hold for a person to review
_URL = re.compile(r"https?://\S+")


class RuleFile(BaseModel):
    id: str
    version: str
    source: str
    title: str  # the source without its link
    url: str | None


class CurrencyLimits(BaseModel):
    currency: str
    auto_approve_minor: int
    daily_cap_minor: int
    ceiling_minor: int
    today_minor: int  # refunds queued, pending or completed today (UTC): what the daily cap counts


class ReplayCase(BaseModel):
    case_id: str
    order_id: str
    order_ref: str
    item: str
    failure_point: str | None
    amount_minor: int | None
    currency: str
    decided_at: datetime
    outcome: Outcome
    # rule | auto | over_max | other_kind | daily_cap | held | dispute | seller_decides | reship
    reason: str


class Replay(BaseModel):
    max_amount_minor: int
    window_days: int
    cases: list[ReplayCase]


class RuleCard(BaseModel):
    failure_point: str
    currency: str
    status: CardStatus
    rule_id: str | None
    max_amount_minor: int | None
    approvals: int
    needed: int
    updated_at: datetime | None
    handled: int
    handled_minor: int
    today_minor: int
    last_run: datetime | None
    replay: Replay | None


class Run(BaseModel):
    case_id: str
    order_id: str
    order_ref: str
    item: str
    rule_id: str
    failure_point: str | None
    amount_minor: int
    currency: str
    at: datetime
    status: str  # queued | pending | completed | simulated | failed | blocked


class AutopilotDesk(BaseModel):
    enabled: bool  # false when the rule file has no seller_rules block
    money_limits: RuleFile
    window_days: int
    limits: list[CurrencyLimits]
    cards: list[RuleCard]
    runs: list[Run]
    statement: Statement


class PolicyDesk(BaseModel):
    policy_text: str
    region: str
    rules: list[RuleFile]  # the liability rule for the region (if any), then money_limits
    own_limits: bool  # the seller set their own limits, so settings facts come from them


def rule_file(rule: RuleSet | MoneyLimits) -> RuleFile:
    """A rule file by id, version and source; `title` is the source without its link."""
    source = rule.source
    found = _URL.search(source)
    title = (source[: found.start()] if found else source).strip().rstrip("—-;:(").strip()
    return RuleFile(
        id=rule.id,
        version=rule.version,
        source=source,
        title=title or source,
        url=found.group(0) if found else None,
    )


class _Replayed:
    """Stands in for the session inside `matching_rule`. Its one query asks for the seller's rule
    of this kind that is on; this answers with the rule being replayed, so the replay runs the
    policy's own allowlist, ceiling and maximum checks, as if that rule were on."""

    def __init__(self, rule: SellerRule) -> None:
        self._rule = rule

    def scalars(self, _statement: object) -> _Replayed:
        return self

    def first(self) -> SellerRule:
        return self._rule


def _ref(order: Order) -> str:
    return order.id[:8].upper()


def _day_start(at: datetime) -> datetime:
    return datetime.combine(at.astimezone(UTC).date(), time.min, tzinfo=UTC)


def replay(
    book: RuleBook,
    seller: Seller,
    rule: SellerRule,
    decided: Sequence[tuple[Decision, Order]],
    refunds: Sequence[MoneyAction],
    *,
    window_days: int,
) -> Replay:
    """AP-12: each decided case, in order, as it would have gone with `rule` on."""
    session = cast(Session, _Replayed(rule))
    cap = effective_limits(book, seller, Currency(rule.currency)).daily_cap.amount_minor
    made: list[tuple[str, datetime, int]] = []  # (case, decided at, amount) the rule refunds
    out: list[ReplayCase] = []
    for d, order in sorted(decided, key=lambda x: (x[0].created_at, x[0].id)):
        last = d.reason_codes[-1] if d.reason_codes else ""
        amount = d.refund_amount_minor
        same_kind = d.failure_point == rule.failure_point
        if last in _PAST_CAP and amount and d.currency == rule.currency:
            at = d.created_at
            start = _day_start(at)
            before = [
                m for m in refunds if m.currency == rule.currency and start <= m.created_at < at
            ]
            already = {m.case_id for m in refunds if m.created_at < at}
            extra = sum(a for cid, t, a in made if start <= t < at and cid not in already)
            if sum(m.amount_minor for m in before) + extra + amount > cap:
                outcome, reason = "you", "daily_cap"
            elif last == Reason.AUTO_REFUND.value:
                outcome, reason = "auto", "auto"
            elif not same_kind:
                # Another rule refunded it, or it waited only for the limit and is another kind.
                outcome, reason = (
                    ("auto", "auto") if last == Reason.SELLER_RULE.value else ("you", "other_kind")
                )
            elif matching_rule(
                session,
                book,
                seller.id,
                FailurePoint(rule.failure_point),
                Money(amount_minor=amount, currency=Currency(rule.currency)),
            ):
                outcome, reason = "rule", "rule"
                made.append((d.case_id, at, amount))
            else:
                outcome, reason = "you", "over_max"
        elif last in _PAST_CAP and amount:  # another currency: this rule changes nothing
            outcome, reason = (
                ("you", "other_kind")
                if last == Reason.OVER_AUTO_APPROVE_LIMIT.value
                else ("auto", "auto")
            )
        else:
            outcome, reason = "you", _LEFT.get(last, "held")
        out.append(
            ReplayCase(
                case_id=d.case_id,
                order_id=order.id,
                order_ref=_ref(order),
                item=order.item_description,
                failure_point=d.failure_point,
                amount_minor=amount,
                currency=d.currency or order.currency,
                decided_at=d.created_at,
                outcome=outcome,
                reason=reason,
            )
        )
    out.reverse()  # newest first, as the page lists them
    return Replay(max_amount_minor=rule.max_amount_minor, window_days=window_days, cases=out)


def _decided(s: Session, seller_id: str, since: datetime) -> list[tuple[Decision, Order]]:
    """Each of the seller's cases with its latest decision, when that decision is in the window."""
    rows = s.execute(
        select(Decision, Order)
        .join(Case, Case.id == Decision.case_id)
        .join(Order, Order.id == Case.order_id)
        .where(Case.seller_id == seller_id)
        .order_by(Decision.created_at, Decision.id)
    ).all()
    latest: dict[str, tuple[Decision, Order]] = {}
    for d, order in rows:
        latest[d.case_id] = (d, order)
    return [x for x in latest.values() if x[0].created_at >= since]


def _counted_refunds(s: Session, seller_id: str, since: datetime) -> list[MoneyAction]:
    """Refunds the daily cap counts (PO-08): queued, pending or completed, made by Doorstep."""
    return list(
        s.scalars(
            select(MoneyAction).where(
                MoneyAction.seller_id == seller_id,
                MoneyAction.kind.in_(_REFUND_KINDS),
                MoneyAction.status.in_(_COUNTED),
                MoneyAction.external.is_(False),
                MoneyAction.created_at >= since,
            )
        )
    )


def _runs(s: Session, seller_id: str) -> list[tuple[Run, MoneyAction | None]]:
    rows = s.execute(
        select(Approval, Decision, Order, MoneyAction)
        .join(Case, Case.id == Approval.case_id)
        .join(Decision, Decision.id == Approval.decision_id)
        .join(Order, Order.id == Case.order_id)
        .outerjoin(
            MoneyAction,
            (MoneyAction.decision_id == Approval.decision_id) & MoneyAction.kind.in_(_REFUND_KINDS),
        )
        .where(
            Case.seller_id == seller_id,
            Approval.approver_kind == ApproverKind.SYSTEM.value,
            Approval.approver_id.startswith(RULE_APPROVER),
        )
        .order_by(Approval.at.desc(), Approval.id)
    ).all()
    return [
        (
            Run(
                case_id=a.case_id,
                order_id=order.id,
                order_ref=_ref(order),
                item=order.item_description,
                rule_id=a.approver_id.removeprefix(RULE_APPROVER),
                failure_point=d.failure_point,
                amount_minor=ma.amount_minor if ma else (d.refund_amount_minor or 0),
                currency=ma.currency if ma else (d.currency or order.currency),
                at=a.at,
                status=money_status(ma) if ma else "queued",
            ),
            ma,
        )
        for a, d, order, ma in rows
    ]


def _preview_book(book: RuleBook) -> RuleBook:
    """The same rule book with the precedent threshold at one approval: `suggest_rules` then shows
    the rule Doorstep would offer for a kind it is still learning. Nothing can be turned on from
    it: `turn_on` reads the real book."""
    limits = book.money_limits
    cfg = limits.seller_rules
    assert cfg is not None
    preview = limits.model_copy(
        update={"seller_rules": cfg.model_copy(update={"min_precedents": 1})}
    )
    return RuleBook(book.liability, preview, book.seller_protection, book.carriers)


def autopilot_desk(s: Session, book: RuleBook, seller: Seller, *, now: datetime) -> AutopilotDesk:
    limits_file = rule_file(book.money_limits)
    cfg = book.money_limits.seller_rules
    spent = statement(s, seller.id, now=now)
    run_rows = _runs(s, seller.id)
    runs = [r for r, _ in run_rows]
    today = _day_start(now)
    home = HOME_CURRENCY.get(seller.region, Currency.GBP).value
    today_by: dict[str, int] = {}
    for cur, total in s.execute(
        select(MoneyAction.currency, func.coalesce(func.sum(MoneyAction.amount_minor), 0))
        .where(
            MoneyAction.seller_id == seller.id,
            MoneyAction.kind.in_(_REFUND_KINDS),
            MoneyAction.status.in_(_COUNTED),
            MoneyAction.external.is_(False),
            MoneyAction.created_at >= today,
            MoneyAction.created_at < today + timedelta(days=1),
        )
        .group_by(MoneyAction.currency)
    ).all():
        today_by[cur] = int(total)
    if cfg is None:
        return AutopilotDesk(
            enabled=False,
            money_limits=limits_file,
            window_days=0,
            limits=[],
            cards=[],
            runs=runs,
            statement=spent,
        )

    since = now - timedelta(days=cfg.window_days)
    rows = {
        (r.failure_point, r.currency): r
        for r in s.scalars(select(SellerRule).where(SellerRule.seller_id == seller.id))
    }
    offered = {
        (x.failure_point.value, x.currency.value): x
        for x in suggest_rules(s, book, seller, now=now)
    }
    learning = {
        (x.failure_point.value, x.currency.value): x
        for x in suggest_rules(s, _preview_book(book), seller, now=now)
    }
    order = [fp.value for fp in cfg.failure_points]
    kinds = sorted(
        {(fp, home) for fp in order} | set(rows) | set(offered) | set(learning),
        key=lambda k: (order.index(k[0]) if k[0] in order else len(order), k[1] != home, k[1]),
    )
    decided = _decided(s, seller.id, since)
    refunds = _counted_refunds(s, seller.id, since - timedelta(days=1))

    cards: list[RuleCard] = []
    for fp, cur in kinds:
        row = rows.get((fp, cur))
        hint: RuleSuggestion | None = offered.get((fp, cur)) or learning.get((fp, cur))
        rule: SellerRule | None = row
        status: CardStatus
        if row is not None:
            status = cast(CardStatus, row.status)
            approvals = len(row.precedents or [])
        elif hint is not None:
            status = "offered" if (fp, cur) in offered else "learning"
            approvals = hint.approved
            rule = SellerRule(  # never added to the session: what the suggestion would create
                seller_id=seller.id,
                failure_point=fp,
                currency=cur,
                max_amount_minor=hint.max_amount_minor,
                status=ON,
                precedents=hint.case_ids,
            )
        else:
            status, approvals = "idle", 0
        mine = [(r, ma) for r, ma in run_rows if row is not None and r.rule_id == row.id]
        cards.append(
            RuleCard(
                failure_point=fp,
                currency=cur,
                status=status,
                rule_id=row.id if row else None,
                max_amount_minor=rule.max_amount_minor if rule else None,
                approvals=approvals,
                needed=cfg.min_precedents,
                updated_at=row.updated_at if row else None,
                handled=len(mine),
                handled_minor=sum(r.amount_minor for r, _ in mine),
                today_minor=sum(
                    r.amount_minor
                    for r, ma in mine
                    if ma is not None and ma.status in _COUNTED and ma.created_at >= today
                ),
                last_run=max((r.at for r, _ in mine), default=None),
                replay=(
                    replay(book, seller, rule, decided, refunds, window_days=cfg.window_days)
                    if rule is not None
                    else None
                ),
            )
        )

    currencies = sorted({c for _, c in kinds} | set(today_by), key=lambda c: (c != home, c))
    limits: list[CurrencyLimits] = []
    for cur in currencies:
        eff = effective_limits(book, seller, Currency(cur))
        limits.append(
            CurrencyLimits(
                currency=cur,
                auto_approve_minor=eff.auto_approve.amount_minor,
                daily_cap_minor=eff.daily_cap.amount_minor,
                ceiling_minor=cfg.ceiling(Currency(cur)).amount_minor,
                today_minor=today_by.get(cur, 0),
            )
        )
    return AutopilotDesk(
        enabled=True,
        money_limits=limits_file,
        window_days=cfg.window_days,
        limits=limits,
        cards=cards,
        runs=runs,
        statement=spent,
    )


def build_automate_desk_router(deps: AppDeps, services: Services, rulebook: RuleBook) -> APIRouter:
    router = APIRouter()
    base = "/api/seller/{token}/automate"

    @router.get(f"{base}/autopilot", response_model=AutopilotDesk)
    def autopilot(token: str, s: Db) -> AutopilotDesk:
        seller = seller_from_link(s, services, token)
        return autopilot_desk(s, rulebook, seller, now=deps.clock.now())

    @router.get(f"{base}/policy", response_model=PolicyDesk)
    def policy(token: str, s: Db) -> PolicyDesk:
        seller = seller_from_link(s, services, token)
        liability = rulebook.select(seller.region)
        files = [rule_file(liability)] if liability is not None else []
        return PolicyDesk(
            policy_text=seller.policy_text or "",
            region=seller.region,
            rules=[*files, rule_file(rulebook.money_limits)],
            own_limits=bool(seller.auto_approve_limits or seller.daily_refund_caps),
        )

    return router
