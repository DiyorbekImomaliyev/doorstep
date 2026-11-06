"""PC over HTTP: the seller's written policy, checked against the rules (seller link only).

Read-only: it reads the seller, their limits and autopilot rules, then asks the checker, which
caches per policy and facts (PC-04). The session is closed before the model call, so a slow
model never holds a database connection.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.ai.policy_check import PolicyCheck, PolicyChecker, build_facts
from app.api.autopilot import seller_from_link
from app.api.deps import AppDeps, Services
from app.api.journey import Db
from app.cases.engine import FailurePoint
from app.money import Currency, Money
from app.policy.autopilot import active_rules
from app.policy.rules import RuleBook, effective_limits

HOME_CURRENCY = {"GB": Currency.GBP, "US": Currency.USD}  # the regions Doorstep supports


def build_policy_check_router(deps: AppDeps, services: Services, rulebook: RuleBook) -> APIRouter:
    router = APIRouter()
    checker = PolicyChecker(services.agent_llm)

    @router.get("/api/seller/{token}/policy-check", response_model=PolicyCheck)
    def policy_check(token: str, s: Db) -> PolicyCheck:
        seller = seller_from_link(s, services, token)
        rule = rulebook.select(seller.region)
        currency = HOME_CURRENCY.get(seller.region)
        if rule is None or currency is None:
            return PolicyCheck(
                status="unchecked",
                issues=[],
                facts=[],
                rule_id=None,
                rule_version=None,
                source="",
                disclaimer=rulebook.seller_protection.disclaimer,
            )
        limits = effective_limits(rulebook, seller, currency)
        autopilot = [
            (
                FailurePoint(r.failure_point),
                Money(amount_minor=r.max_amount_minor, currency=currency),
            )
            for r in active_rules(s, seller.id)
            if r.currency == currency.value
        ]
        policy = seller.policy_text or ""
        facts = build_facts(
            rule, auto_limit=limits.auto_approve, daily_cap=limits.daily_cap, autopilot=autopilot
        )
        s.close()
        return checker.check(policy, facts, rule)

    return router
