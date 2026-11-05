"""Carrier claim terms (RC-01, RC-02, ADR-037): rules/carriers/carrier_claims_v1.yaml.

A business rule like every other (hard rule 2), so code only reads it. It lives in its own folder
because it is not a liability rule: it never decides who pays the buyer, only what a seller can
claim back from a carrier and by when. Loaded and validated strictly with the rule book at
startup; an invalid or missing file stops startup. An unknown carrier uses the `default` entry.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, Field, StrictInt, StrictStr, ValidationError

from app.money import Currency
from app.policy.rules import RuleValidationError, _RuleBase, _Strict

CARRIER_DIR = "carriers"
CARRIER_FILE = "carrier_claims_v1.yaml"
DEFAULT = "default"
_ID = re.compile(r"[a-z][a-z0-9_]*")


def normalise(carrier: str) -> str:
    """'Royal Mail' / 'royal-mail' / 'ROYAL_MAIL' -> 'royal_mail'."""
    return re.sub(r"[^a-z0-9]+", "_", carrier.strip().lower()).strip("_")


class CarrierEntry(_Strict):
    name: StrictStr = Field(min_length=1)
    aliases: list[Annotated[StrictStr, Field(pattern=_ID.pattern)]] = Field(default_factory=list)
    service_basis: StrictStr = Field(min_length=1)
    standard_cover_minor: dict[Currency, Annotated[StrictInt, Field(ge=1)]] = Field(
        default_factory=dict
    )
    loss_window_days: StrictInt = Field(ge=1)
    damage_window_days: StrictInt = Field(ge=1)
    window_starts: Literal["posting", "delivery"]
    source: StrictStr = Field(min_length=1)
    terms_url: StrictStr = Field(pattern=r"^(https://\S+)?$")
    note: StrictStr = ""


class CarrierClaimsFile(_RuleBase):
    kind: Literal["carrier_claims"]
    disclaimer: StrictStr = Field(min_length=1)
    carriers: dict[Annotated[StrictStr, Field(pattern=_ID.pattern)], CarrierEntry]

    def model_post_init(self, _: Any) -> None:
        if DEFAULT not in self.carriers:
            raise ValueError(f"carriers must include a {DEFAULT!r} entry")
        seen: dict[str, str] = {}
        for carrier_id, entry in self.carriers.items():
            for name in (carrier_id, *entry.aliases):
                if name in seen:
                    raise ValueError(f"{name!r} names both {seen[name]} and {carrier_id}")
                seen[name] = carrier_id


class ClaimTerms(BaseModel):
    """What one recovery can claim: computed in code from the file, never by a model."""

    carrier_id: str
    carrier_name: str
    is_default: bool
    currency: Currency
    item_minor: int
    cover_minor: int | None  # None: the file has no cover for this carrier and currency
    claim_minor: int
    window_days: int
    window_starts: str
    deadline_at: datetime
    service_basis: str
    source: str
    terms_url: str
    note: str
    rule_id: str
    rule_version: str


class CarrierTerms:
    """The validated carrier file. Built only by load_carrier_terms()."""

    def __init__(self, data: CarrierClaimsFile) -> None:
        self.data = data
        self.rule_id = data.id
        self.rule_version = data.version
        self.disclaimer = data.disclaimer
        self._by_name = {
            name: carrier_id
            for carrier_id, entry in data.carriers.items()
            for name in (carrier_id, *entry.aliases)
        }

    def entry(self, carrier: str) -> tuple[str, CarrierEntry]:
        carrier_id = self._by_name.get(normalise(carrier), DEFAULT)
        return carrier_id, self.data.carriers[carrier_id]

    def claim(
        self,
        *,
        carrier: str,
        currency: Currency,
        item_minor: int,
        damage: bool,
        posted_at: datetime,
        delivered_at: datetime | None,
    ) -> ClaimTerms:
        """Claim the item value, capped by the standard cover for the currency; the deadline runs
        from posting, or from delivery when the carrier counts from delivery and a delivered scan
        exists (otherwise from posting, which is earlier and so never late)."""
        carrier_id, entry = self.entry(carrier)
        cover = entry.standard_cover_minor.get(currency)
        window = entry.damage_window_days if damage else entry.loss_window_days
        start = (
            delivered_at
            if entry.window_starts == "delivery" and delivered_at is not None
            else posted_at
        )
        return ClaimTerms(
            carrier_id=carrier_id,
            carrier_name=entry.name,
            is_default=carrier_id == DEFAULT,
            currency=currency,
            item_minor=item_minor,
            cover_minor=cover,
            claim_minor=item_minor if cover is None else min(item_minor, cover),
            window_days=window,
            window_starts=entry.window_starts,
            deadline_at=start + timedelta(days=window),
            service_basis=entry.service_basis,
            source=entry.source,
            terms_url=entry.terms_url,
            note=entry.note,
            rule_id=self.rule_id,
            rule_version=self.rule_version,
        )


def load_carrier_terms(rules_dir: Path) -> CarrierTerms:
    """rules_dir/carriers/carrier_claims_v1.yaml, validated strictly (RC-02)."""
    path = rules_dir / CARRIER_DIR / CARRIER_FILE
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))  # pragma: no mutate
    except OSError as exc:
        raise RuleValidationError(f"{CARRIER_DIR}/{CARRIER_FILE}: cannot read: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RuleValidationError(f"{CARRIER_FILE}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RuleValidationError(f"{CARRIER_FILE}: top level must be a mapping")
    try:
        parsed = CarrierClaimsFile.model_validate(data)
    except (ValidationError, ValueError) as exc:
        raise RuleValidationError(f"{CARRIER_FILE}: {exc}") from exc
    if parsed.id != path.stem:
        raise RuleValidationError(f"{CARRIER_FILE}: id {parsed.id!r} must equal the file name")
    return CarrierTerms(parsed)
