"""What would have changed this decision, and how to prevent the next one (CF-01, CF-02).

Both are computed in code from the rule file and the seller's own cases; no model is involved.

- CF-01: each decision states which possession proof from the matching rule file would have
  changed the liable party, or which one was present. It is stored on the decision, so it
  replays with the rule version it used.
- CF-02: the seller's prevention suggestions group their seller-liable cases by delivery method
  and value band, and suggest the safer method for the bands that cost them most.

The wording never blames the buyer (hard rule 7): it names proofs, not people.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.explainer import money_text
from app.money import Currency, Money
from app.orders.enums import ProofKind
from app.orders.models import Case, Decision, Order, Shipment
from app.policy.rules import RuleSet
from tuning import engine_config as cfg

PROOF_TEXT: dict[ProofKind, str] = {
    ProofKind.SIGNATURE: "a signature",
    ProofKind.LOCKER_PICKUP_CODE: "a locker pickup with the buyer's code",
    ProofKind.PHOTO_AT_BUYER_ADDRESS: "a safe-place photo at the buyer's address",
    ProofKind.BUYER_SEAL_SCAN: "a scan of the seal packed inside the parcel",
    ProofKind.DELIVERED_SCAN: "a delivered scan",
    ProofKind.DELIVERY_PHOTO: "a delivery photo",
}


def _one_of(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} or {items[-1]}"


def _all_of(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} together with {items[-1]}"


class Counterfactual(BaseModel):
    """CF-01. `would_change` lists proofs any ONE of which would have changed the liable party;
    `would_change_together` is the rule's all-of set, when it has one. When the carrier's data
    contradicted its scan and the rule discounts such scans, only a delivered scan that the
    carrier's own data supports would count (`scan_must_be_supported`), and the text says so."""

    rule_id: str | None
    rule_version: str | None
    possession_proven: bool
    present: list[ProofKind]
    would_change: list[ProofKind]
    would_change_together: list[ProofKind]
    scan_discounted: bool
    scan_must_be_supported: bool = False
    text: str


def counterfactual(
    rule: RuleSet | None, proofs: frozenset[ProofKind], *, scan_contradicted: bool = False
) -> Counterfactual:
    if rule is None:
        return Counterfactual(
            rule_id=None,
            rule_version=None,
            possession_proven=False,
            present=[],
            would_change=[],
            would_change_together=[],
            scan_discounted=False,
            text="No rule set matched this order, so a person reviews it; no single proof "
            "would have changed that.",
        )
    scan_discounted = (
        scan_contradicted
        and not rule.contradicted_scan_counts_as_proof
        and ProofKind.DELIVERED_SCAN in proofs
    )
    must_be_supported = scan_contradicted and not rule.contradicted_scan_counts_as_proof

    def label(p: ProofKind) -> str:
        if p == ProofKind.DELIVERED_SCAN and must_be_supported:
            return "a delivered scan that the carrier's own data supports"
        return PROOF_TEXT[p]

    effective = proofs - {ProofKind.DELIVERED_SCAN} if scan_discounted else proofs
    proven = rule.possession_proven(effective)
    together = list(rule.proof_required)
    note = (
        " The carrier's delivered scan did not count: the carrier's own data contradicts it."
        if scan_discounted
        else ""
    )
    if proven:
        present = [p for p in rule.possession_proof if p in effective]
        if together and set(together) <= effective:
            present += [p for p in together if p not in present]
        return Counterfactual(
            rule_id=rule.id,
            rule_version=rule.version,
            possession_proven=True,
            present=present,
            would_change=[],
            would_change_together=[],
            scan_discounted=scan_discounted,
            text=f"Possession was proven under {rule.id} by "
            f"{_one_of([PROOF_TEXT[p] for p in present])}.{note}",
        )
    same_party = (
        rule.when_possession_proven.liable_party == rule.when_no_possession_proof.liable_party
    )
    if same_party:
        return Counterfactual(
            rule_id=rule.id,
            rule_version=rule.version,
            possession_proven=False,
            present=[],
            would_change=[],
            would_change_together=[],
            scan_discounted=scan_discounted,
            text=f"Under {rule.id}, no proof changes who is responsible.{note}",
        )
    any_one = list(rule.possession_proof)
    parts = []
    if any_one:
        parts.append(_one_of([label(p) for p in any_one]))
    if together:
        parts.append(_all_of([label(p) for p in together]))
    party = rule.when_possession_proven.liable_party
    return Counterfactual(
        rule_id=rule.id,
        rule_version=rule.version,
        possession_proven=False,
        present=[],
        would_change=any_one,
        would_change_together=together,
        scan_discounted=scan_discounted,
        scan_must_be_supported=must_be_supported
        and ProofKind.DELIVERED_SCAN in (*any_one, *together),
        text=f"Under {rule.id}, {', or '.join(parts)} would have made this the {party}'s risk."
        f"{note}",
    )


# ------------------------------------------------------------------- CF-02 prevention


class PreventionSuggestion(BaseModel):
    method: str
    currency: str
    band: str
    band_min_minor: int
    band_max_minor: int | None
    cases: int  # seller-liable cases in this method and band
    orders: int  # all shipped orders in this method and band
    money_minor: int  # order value of those cases
    suggested_method: str
    text: str


SAFER_METHOD = {"home": "locker", "locker": "signature"}  # signature has no safer option
_METHOD_TEXT = {"home": "home delivery", "locker": "locker pickup", "signature": "signature"}


def _band(amount_minor: int) -> tuple[int, int | None]:
    low = 0
    for edge in cfg.PREVENTION_VALUE_BANDS_MINOR:
        if amount_minor < edge:
            return low, edge
        low = edge
    return low, None


def _short(amount_minor: int, currency: Currency) -> str:
    """£25 rather than £25.00, for band labels."""
    text = money_text(Money(amount_minor=amount_minor, currency=currency))
    return text.removesuffix(".00") if text.endswith(".00") else text.replace(".00 ", " ")


def _band_text(low: int, high: int | None, currency: Currency) -> str:
    if high is None:
        return f"{_short(low, currency)} and over"
    if low == 0:
        return f"under {_short(high, currency)}"
    return f"{_short(low, currency)} to {_short(high, currency)}"


def _seller_liable(decision: Decision) -> bool:
    liability: dict[str, Any] = decision.signals.get("liability") or {}
    return liability.get("liable_party") == "seller"


def prevention(session: Session, seller_id: str) -> list[PreventionSuggestion]:
    """CF-02: suggestions from the seller's own seller-liable cases, most money first."""
    shipped = session.execute(
        select(Order, Shipment.method)
        .join(Shipment, Shipment.order_id == Order.id)
        .where(Order.seller_id == seller_id)
    ).all()
    orders: dict[tuple[str, str, int], int] = defaultdict(int)
    method_of: dict[str, str] = {}
    for order, method in shipped:
        method = method or "home"
        method_of.setdefault(order.id, method)
        orders[(method, order.currency, _band(order.amount_minor)[0])] += 1

    latest: dict[str, Decision] = {}
    rows = session.execute(
        select(Decision, Case)
        .join(Case, Case.id == Decision.case_id)
        .where(Case.seller_id == seller_id)
        .order_by(Decision.created_at)
    ).all()
    for decision, case in rows:
        latest[case.order_id] = decision  # the newest decision per order wins

    cases: dict[tuple[str, str, int], int] = defaultdict(int)
    money: dict[tuple[str, str, int], int] = defaultdict(int)
    for order_id, decision in latest.items():
        if not _seller_liable(decision) or order_id not in method_of:
            continue
        order = session.get(Order, order_id)
        assert order is not None
        key = (method_of[order_id], order.currency, _band(order.amount_minor)[0])
        cases[key] += 1
        money[key] += order.amount_minor

    out: list[PreventionSuggestion] = []
    for key, n in cases.items():
        method, currency, low = key
        safer = SAFER_METHOD.get(method)
        if n < cfg.PREVENTION_MIN_CASES or safer is None:
            continue
        _, high = _band(low)
        cur = Currency(currency)
        band = _band_text(low, high, cur)
        paid = money_text(Money(amount_minor=money[key], currency=cur))
        why = (
            "a locker needs the buyer's pickup code"
            if safer == "locker"
            else "a signature proves who received it"
        )
        out.append(
            PreventionSuggestion(
                method=method,
                currency=currency,
                band=band,
                band_min_minor=low,
                band_max_minor=high,
                cases=n,
                orders=orders[key],
                money_minor=money[key],
                suggested_method=safer,
                text=f"{n} of your {orders[key]} {_METHOD_TEXT[method]} orders of {band} became "
                f"cases you paid for ({paid}). "
                f"Offer {_METHOD_TEXT[safer]} for this band: {why}, which proves possession.",
            )
        )
    out.sort(key=lambda p: (-p.money_minor, -p.cases, p.method, p.band_min_minor))
    return out[: cfg.PREVENTION_MAX_SUGGESTIONS]
