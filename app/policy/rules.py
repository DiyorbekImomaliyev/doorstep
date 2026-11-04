"""Rule loader. Rules live in rules/*.yaml (hard rule 2); code only reads them."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from app.money import Currency, Money
from app.orders.enums import BuyerType, Fix, ProofKind
from app.orders.models import Seller
from app.orders.schemas import Address


class RuleValidationError(Exception):
    pass


class LiabilityOutcome(BaseModel):
    rule_id: str
    rule_version: str
    possession_proven: bool
    liable_party: str  # "seller" | "buyer" | "undetermined"
    recommended_fix: list[Fix]


class RuleSet:
    """One liability rule file (uk_possession_v1, eu_possession_v1, us_seller_policy_v1)."""

    id: str
    version: str
    source: str

    def evaluate(self, proofs: frozenset[ProofKind]) -> LiabilityOutcome:
        raise NotImplementedError


class MoneyLimits:
    """rules/money_limits_v1.yaml (RL-11, ADR-006)."""

    id: str
    version: str
    repeat_claims_threshold: int
    repeat_claims_window_days: int

    def auto_approve_limit(self, currency: Currency) -> Money:
        raise NotImplementedError

    def daily_cap(self, currency: Currency) -> Money:
        raise NotImplementedError


class SellerProtection:
    """rules/paypal_sp_v1.yaml (RL-07, RL-10)."""

    id: str
    version: str

    def is_redirected(self, order_address: Address, delivery_address: Address) -> bool:
        raise NotImplementedError

    def signature_recommended(self, amount: Money) -> bool:
        raise NotImplementedError


class SellerLimits(BaseModel):
    auto_approve: Money
    daily_cap: Money


class RuleBook:
    rule_ids: list[str]
    money_limits: MoneyLimits
    seller_protection: SellerProtection

    def select(
        self, buyer_country: str, buyer_type: BuyerType = BuyerType.CONSUMER
    ) -> RuleSet | None:
        """None when no rule set applies (-> HOLD_FOR_REVIEW, RL-03)."""
        raise NotImplementedError


def load_rules(directory: Path) -> RuleBook:
    """Validate every file; raise RuleValidationError on any invalid or overlapping rule."""
    raise NotImplementedError


def effective_limits(book: RuleBook, seller: Seller, currency: Currency) -> SellerLimits:
    """Seller overrides per currency, else the money_limits_v1 defaults."""
    raise NotImplementedError
