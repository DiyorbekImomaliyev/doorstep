"""AP-02/05/06/07 over HTTP: the seller's autopilot suggestions, rules and statement (seller link
only).

Turning a rule on takes no amount: the policy uses the current suggestion's maximum (AP-02).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import AppDeps, Services
from app.api.journey import SELLER, Db
from app.cases.engine import FailurePoint
from app.money import Currency
from app.orders.models import Seller, SellerRule
from app.policy.autopilot import (
    AutopilotError,
    RuleSuggestion,
    Statement,
    active_rules,
    dismiss,
    statement,
    suggest_rules,
    turn_off,
    turn_on,
)
from app.policy.rules import RuleBook
from app.security.links import InvalidLink


class RuleOut(BaseModel):
    id: str
    failure_point: str
    currency: str
    max_amount_minor: int
    status: str
    precedents: int
    updated_at: datetime


class AutopilotOut(BaseModel):
    rules: list[RuleOut]
    suggestions: list[RuleSuggestion]


class RuleKindIn(BaseModel):
    failure_point: FailurePoint
    currency: Currency


def _out(rule: SellerRule) -> RuleOut:
    return RuleOut(
        id=rule.id,
        failure_point=rule.failure_point,
        currency=rule.currency,
        max_amount_minor=rule.max_amount_minor,
        status=rule.status,
        precedents=len(rule.precedents or []),
        updated_at=rule.updated_at,
    )


def seller_from_link(s: Session, services: Services, token: str) -> Seller:
    """The seller a signed seller link names; 404 for a bad link or an unknown seller."""
    try:
        seller = s.get(Seller, services.links.verify(token, scope=SELLER))
    except InvalidLink:
        seller = None
    if seller is None:
        raise HTTPException(404, "not found")
    return seller


def build_autopilot_router(deps: AppDeps, services: Services, rulebook: RuleBook) -> APIRouter:
    router = APIRouter()
    base = "/api/seller/{token}/autopilot"

    def _seller(s: Session, token: str) -> Seller:
        return seller_from_link(s, services, token)

    @router.get(base, response_model=AutopilotOut)
    def autopilot(token: str, s: Db) -> AutopilotOut:
        seller = _seller(s, token)
        return AutopilotOut(
            rules=[_out(r) for r in active_rules(s, seller.id)],
            suggestions=suggest_rules(s, rulebook, seller, now=deps.clock.now()),
        )

    @router.get(f"{base}/statement", response_model=Statement)
    def autopilot_statement(token: str, s: Db) -> Statement:
        """AP-07: what the seller's rules refunded in the last 7 days, and what carriers owe."""
        return statement(s, _seller(s, token).id, now=deps.clock.now())

    @router.post(base, response_model=RuleOut)
    def rule_on(token: str, body: RuleKindIn, s: Db) -> RuleOut:
        seller = _seller(s, token)
        try:
            rule = turn_on(
                s,
                rulebook,
                seller,
                body.failure_point,
                body.currency.value,
                now=deps.clock.now(),
                actor=f"seller:{seller.id}",
            )
        except AutopilotError as exc:
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return _out(rule)

    @router.post(f"{base}/dismiss", response_model=RuleOut)
    def rule_dismiss(token: str, body: RuleKindIn, s: Db) -> RuleOut:
        seller = _seller(s, token)
        try:
            rule = dismiss(
                s,
                rulebook,
                seller,
                body.failure_point,
                body.currency.value,
                now=deps.clock.now(),
                actor=f"seller:{seller.id}",
            )
        except AutopilotError as exc:
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return _out(rule)

    @router.post(f"{base}/{{rule_id}}/off", response_model=RuleOut)
    def rule_off(token: str, rule_id: str, s: Db) -> RuleOut:
        seller = _seller(s, token)
        try:
            rule = turn_off(s, seller, rule_id, now=deps.clock.now(), actor=f"seller:{seller.id}")
        except AutopilotError as exc:
            raise HTTPException(404, str(exc)) from None
        s.commit()
        return _out(rule)

    return router
