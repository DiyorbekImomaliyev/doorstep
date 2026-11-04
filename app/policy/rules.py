"""Rule loader. Rules live in rules/*.yaml (hard rule 2); code only reads them.

Each file declares a ``kind``. Liability files decide who carries the loss; ``seller_protection``
holds the PayPal checks; ``money_limits`` holds the default limits. Every file is validated
strictly at startup: unknown keys, missing fields, an unquoted (date) version, a filename that
differs from the ``id``, or two liability rules matching one buyer all stop startup (RL-01..03).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from itertools import combinations
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from app.money import Currency, Money
from app.orders.enums import BuyerType, Fix, ProofKind
from app.orders.models import Seller
from app.orders.schemas import Address

LiableParty = Literal["seller", "buyer", "undetermined"]
_COUNTRY = re.compile(r"[A-Z]{2}")


class RuleValidationError(Exception):
    pass


class LiabilityOutcome(BaseModel):
    rule_id: str
    rule_version: str
    possession_proven: bool
    liable_party: str  # "seller" | "buyer" | "undetermined"
    recommended_fix: list[Fix]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _RuleBase(_Strict):
    id: StrictStr = Field(min_length=1)
    version: StrictStr = Field(min_length=1)  # quoted string; a YAML date fails (RL-02)
    source: StrictStr = Field(min_length=1)


class AppliesIf(_Strict):
    buyer_country: list[Annotated[StrictStr, Field(pattern=_COUNTRY.pattern)]] = Field(min_length=1)
    buyer_type: BuyerType = BuyerType.CONSUMER


class OutcomeSpec(_Strict):
    liable_party: LiableParty
    recommended_fix: list[Fix]
    also: StrictStr = ""


class RuleSet(_RuleBase):
    """One liability rule file (uk_possession_v1, eu_possession_v1, us_seller_policy_v1)."""

    kind: Literal["liability"]
    region: StrictStr = Field(min_length=2)
    applies_if: AppliesIf
    # Any ONE of these proves possession.
    possession_proof: list[ProofKind] = Field(default_factory=list)
    # ALL of these together prove possession (US structured seller policy, RL-06).
    proof_required: list[ProofKind] = Field(default_factory=list)
    when_no_possession_proof: OutcomeSpec
    when_possession_proven: OutcomeSpec
    policy_text: StrictStr = ""  # RAG / explanations only; never read by evaluate()
    disclaimer: StrictStr = Field(min_length=1)

    def matches(self, buyer_country: str, buyer_type: BuyerType) -> bool:
        return (
            buyer_country.upper() in self.applies_if.buyer_country
            and buyer_type == self.applies_if.buyer_type
        )

    def possession_proven(self, proofs: frozenset[ProofKind]) -> bool:
        any_one = any(p in proofs for p in self.possession_proof)
        all_required = bool(self.proof_required) and set(self.proof_required) <= proofs
        return any_one or all_required

    def evaluate(self, proofs: frozenset[ProofKind]) -> LiabilityOutcome:
        proven = self.possession_proven(proofs)
        spec = self.when_possession_proven if proven else self.when_no_possession_proof
        return LiabilityOutcome(
            rule_id=self.id,
            rule_version=self.version,
            possession_proven=proven,
            liable_party=spec.liable_party,
            recommended_fix=list(spec.recommended_fix),
        )


def _per_currency(values: dict[Currency, int], name: str) -> dict[Currency, int]:
    missing = set(Currency) - set(values)
    if missing:
        raise ValueError(f"{name} missing currencies: {sorted(missing)}")
    return values


class RepeatClaims(_Strict):
    threshold: StrictInt = Field(ge=1)
    window_days: StrictInt = Field(ge=1)


class MoneyLimits(_RuleBase):
    """rules/money_limits_v1.yaml (RL-11, ADR-006)."""

    kind: Literal["money_limits"]
    auto_approve_limit_minor: dict[Currency, Annotated[StrictInt, Field(ge=0)]]
    daily_refund_cap_minor: dict[Currency, Annotated[StrictInt, Field(ge=0)]]
    repeat_claims: RepeatClaims

    def model_post_init(self, _: Any) -> None:
        _per_currency(self.auto_approve_limit_minor, "auto_approve_limit_minor")
        _per_currency(self.daily_refund_cap_minor, "daily_refund_cap_minor")

    @property
    def repeat_claims_threshold(self) -> int:
        return self.repeat_claims.threshold

    @property
    def repeat_claims_window_days(self) -> int:
        return self.repeat_claims.window_days

    def auto_approve_limit(self, currency: Currency) -> Money:
        return Money(amount_minor=self.auto_approve_limit_minor[currency], currency=currency)

    def daily_cap(self, currency: Currency) -> Money:
        return Money(amount_minor=self.daily_refund_cap_minor[currency], currency=currency)


AddressField = Literal["name", "line1", "line2", "city", "postal_code", "country"]


def _norm(value: str) -> str:
    """Case-, space- and punctuation-insensitive form of an address field."""
    return re.sub(r"[^0-9a-z]", "", value.casefold())


class SellerProtection(_RuleBase):
    """rules/paypal_sp_v1.yaml (RL-07, RL-10)."""

    kind: Literal["seller_protection"]
    redirect_compare_fields: list[AddressField] = Field(min_length=1)
    signature_recommended_over_minor: dict[Currency, Annotated[StrictInt, Field(ge=0)]]
    disclaimer: StrictStr = Field(min_length=1)

    def is_redirected(self, order_address: Address, delivery_address: Address) -> bool:
        return any(
            _norm(getattr(order_address, f)) != _norm(getattr(delivery_address, f))
            for f in self.redirect_compare_fields
        )

    def signature_recommended(self, amount: Money) -> bool:
        threshold = self.signature_recommended_over_minor.get(amount.currency)
        return threshold is not None and amount.amount_minor > threshold


class SellerLimits(BaseModel):
    auto_approve: Money
    daily_cap: Money


class RuleBook:
    """Every validated rule file. Built only by load_rules()."""

    def __init__(
        self,
        liability: Iterable[RuleSet],
        money_limits: MoneyLimits,
        seller_protection: SellerProtection,
    ) -> None:
        self.liability = list(liability)
        self.money_limits = money_limits
        self.seller_protection = seller_protection
        self.rule_ids: list[str] = [r.id for r in self.liability] + [
            money_limits.id,
            seller_protection.id,
        ]

    def select(
        self, buyer_country: str, buyer_type: BuyerType = BuyerType.CONSUMER
    ) -> RuleSet | None:
        """None when no rule set applies (-> HOLD_FOR_REVIEW, RL-03)."""
        for rule in self.liability:
            if rule.matches(buyer_country, buyer_type):
                return rule
        return None


_KINDS: dict[str, type[_RuleBase]] = {
    "liability": RuleSet,
    "money_limits": MoneyLimits,
    "seller_protection": SellerProtection,
}


def _load_file(path: Path) -> _RuleBase:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuleValidationError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RuleValidationError(f"{path.name}: top level must be a mapping")
    kind = data.get("kind")
    model = _KINDS.get(kind) if isinstance(kind, str) else None
    if model is None:
        raise RuleValidationError(f"{path.name}: unknown kind {kind!r}; expected {sorted(_KINDS)}")
    try:
        rule = model.model_validate(data)
    except (ValidationError, ValueError) as exc:
        raise RuleValidationError(f"{path.name}: {exc}") from exc
    if rule.id != path.stem:
        raise RuleValidationError(f"{path.name}: id {rule.id!r} must equal the file name")
    return rule


def _exactly_one[T](rules: list[T], kind: str) -> T:
    if len(rules) != 1:
        raise RuleValidationError(f"expected exactly one {kind} rule file, found {len(rules)}")
    return rules[0]


def load_rules(directory: Path) -> RuleBook:
    """Validate every file; raise RuleValidationError on any invalid or overlapping rule."""
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise RuleValidationError(f"no rule files in {directory}")
    rules = [_load_file(p) for p in paths]
    liability = [r for r in rules if isinstance(r, RuleSet)]
    for a, b in combinations(liability, 2):
        same_type = a.applies_if.buyer_type == b.applies_if.buyer_type
        shared = set(a.applies_if.buyer_country) & set(b.applies_if.buyer_country)
        if same_type and shared:
            raise RuleValidationError(
                f"{a.id} and {b.id} both match {a.applies_if.buyer_type} buyers in {sorted(shared)}"
            )
    return RuleBook(
        liability,
        _exactly_one([r for r in rules if isinstance(r, MoneyLimits)], "money_limits"),
        _exactly_one([r for r in rules if isinstance(r, SellerProtection)], "seller_protection"),
    )


def effective_limits(book: RuleBook, seller: Seller, currency: Currency) -> SellerLimits:
    """Seller overrides per currency, else the money_limits_v1 defaults."""
    defaults = book.money_limits
    auto = (seller.auto_approve_limits or {}).get(currency.value)
    cap = (seller.daily_refund_caps or {}).get(currency.value)
    return SellerLimits(
        auto_approve=(
            Money(amount_minor=auto, currency=currency)
            if auto is not None
            else defaults.auto_approve_limit(currency)
        ),
        daily_cap=(
            Money(amount_minor=cap, currency=currency)
            if cap is not None
            else defaults.daily_cap(currency)
        ),
    )
