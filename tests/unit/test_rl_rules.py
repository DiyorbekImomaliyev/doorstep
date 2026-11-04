"""RL — Rules engine. Rules live in rules/*.yaml; tests break copies of them on purpose."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from app.api.deps import AppDeps
from app.main import create_app
from app.money import Currency
from app.orders.enums import BuyerType, CaseState, Fix, ProofKind
from app.orders.models import Decision
from app.orders.schemas import Address
from app.policy.rules import RuleBook, RuleValidationError, effective_limits, load_rules
from app.policy.service import PolicyService
from tests.conftest import ROOT, RULE_FILES, RULES_DIR
from tests.factories import make_case, make_order, make_seller
from tests.fakes import FakeVerifier
from tests.helpers import engine_result, facts_for, money

P = ProofKind


def _edit(path: Path, change: Callable[[dict], None]) -> None:
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _no_model_calls(monkeypatch) -> None:
    import app.ai.llm

    def boom(**_: object) -> None:
        raise AssertionError("a model was called during a rule/engine decision")

    monkeypatch.setattr(app.ai.llm, "complete_structured", boom)


# ---------------------------------------------------------------- RL-01


def test_RL_01_real_rule_files_load(rulebook: RuleBook):
    assert sorted(rulebook.rule_ids) == sorted(f.removesuffix(".yaml") for f in RULE_FILES)


@pytest.mark.parametrize(
    "breakage",
    [
        lambda d: d.pop("applies_if"),
        lambda d: d.update(applies_if="GB"),
        lambda d: d.pop("disclaimer"),
        lambda d: d.pop("region"),
        lambda d: d.update(unexpected_key=True),
    ],
    ids=[
        "missing-applies_if",
        "applies_if-not-a-mapping",
        "missing-disclaimer",
        "missing-region",
        "unknown-key",
    ],
)
def test_RL_01_invalid_rule_file_fails_validation(rules_copy, breakage):
    _edit(rules_copy / "uk_possession_v1.yaml", breakage)
    with pytest.raises(RuleValidationError):
        load_rules(rules_copy)


def test_RL_01_app_startup_fails_on_invalid_rule_file(rules_copy, session_factory, settings, clock):
    _edit(rules_copy / "uk_possession_v1.yaml", lambda d: d.pop("applies_if"))
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=FakeVerifier(),
        clock=clock,
        rules_dir=rules_copy,
    )
    with pytest.raises(RuleValidationError):
        create_app(deps)


# ---------------------------------------------------------------- RL-02


@pytest.mark.parametrize("filename", RULE_FILES)
@pytest.mark.parametrize("key", ["id", "version", "source"])
def test_RL_02_every_rule_needs_id_version_source(rules_copy, filename, key):
    _edit(rules_copy / filename, lambda d: d.pop(key))
    with pytest.raises(RuleValidationError):
        load_rules(rules_copy)


@pytest.mark.parametrize("filename", RULE_FILES)
def test_RL_02_unquoted_date_version_fails_validation(rules_copy, filename):
    path = rules_copy / filename
    text = path.read_text()
    changed = re.sub(r'^version:\s*["\']([^"\']+)["\']', r"version: \1", text, flags=re.M)
    assert changed != text, "version must be written as a quoted string"
    path.write_text(changed)
    with pytest.raises(RuleValidationError):
        load_rules(rules_copy)


# ---------------------------------------------------------------- RL-03


@pytest.mark.parametrize(
    ("country", "rule_id"),
    [
        ("GB", "uk_possession_v1"),
        ("FR", "eu_possession_v1"),
        ("DE", "eu_possession_v1"),
        ("US", "us_seller_policy_v1"),
    ],
)
def test_RL_03_rule_set_chosen_by_country_and_type(rulebook, country, rule_id):
    rule = rulebook.select(country, BuyerType.CONSUMER)
    assert rule is not None and rule.id == rule_id


def test_RL_03_buyer_type_defaults_to_consumer(rulebook):
    assert rulebook.select("GB").id == rulebook.select("GB", BuyerType.CONSUMER).id  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("country", "buyer_type"), [("CA", BuyerType.CONSUMER), ("GB", BuyerType.BUSINESS)]
)
def test_RL_03_no_matching_rule_set_returns_none(rulebook, country, buyer_type):
    assert rulebook.select(country, buyer_type) is None


def test_RL_03_no_matching_rule_set_holds_for_review(db, rulebook, clock):
    order = make_order(
        db, make_seller(db, region="US"), buyer_country="CA", currency="USD", amount_minor=2000
    )
    case = make_case(db, order)
    decision = PolicyService(db, rulebook, clock).decide(case, engine_result(), facts_for(order))
    assert decision.route == CaseState.HOLD_FOR_REVIEW
    assert case.state == CaseState.HOLD_FOR_REVIEW


def test_RL_03_overlapping_rule_sets_stop_startup(rules_copy):
    data = yaml.safe_load((rules_copy / "uk_possession_v1.yaml").read_text())
    data["id"] = "uk_possession_copy_v1"
    (rules_copy / "uk_possession_copy_v1.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(RuleValidationError):
        load_rules(rules_copy)


# ---------------------------------------------------------------- RL-04 / RL-05


@pytest.mark.parametrize("country", ["GB", "FR"])
@pytest.mark.parametrize(
    "proofs",
    [frozenset(), frozenset({P.DELIVERED_SCAN})],
    ids=["no-proof", "delivered-scan-is-not-possession"],
)
def test_RL_04_uk_eu_consumer_without_possession_proof_seller_liable(rulebook, country, proofs):
    outcome = rulebook.select(country).evaluate(proofs)  # type: ignore[union-attr]
    assert outcome.possession_proven is False
    assert outcome.liable_party == "seller"
    assert Fix.REFUND in outcome.recommended_fix and Fix.RESHIP in outcome.recommended_fix


@pytest.mark.parametrize("country", ["GB", "FR"])
@pytest.mark.parametrize("proof", [P.LOCKER_PICKUP_CODE, P.SIGNATURE])
def test_RL_05_locker_code_or_signature_proves_possession(rulebook, country, proof):
    outcome = rulebook.select(country).evaluate(frozenset({proof}))  # type: ignore[union-attr]
    assert outcome.possession_proven is True
    assert outcome.liable_party == "buyer"


# ---------------------------------------------------------------- RL-06


@pytest.mark.parametrize(
    ("proofs", "proven"),
    [
        (frozenset({P.DELIVERED_SCAN, P.DELIVERY_PHOTO}), True),
        (frozenset({P.DELIVERED_SCAN}), False),
        (frozenset(), False),
    ],
)
def test_RL_06_us_structured_policy_decides_liability(rulebook, proofs, proven):
    outcome = rulebook.select("US").evaluate(proofs)  # type: ignore[union-attr]
    assert outcome.rule_id == "us_seller_policy_v1"
    assert outcome.possession_proven is proven
    assert outcome.liable_party == ("buyer" if proven else "seller")


def test_RL_06_us_policy_fields_read_from_yaml(rules_copy):
    _edit(
        rules_copy / "us_seller_policy_v1.yaml",
        lambda d: d.update(proof_required=["delivered_scan"]),
    )
    outcome = load_rules(rules_copy).select("US").evaluate(frozenset({P.DELIVERED_SCAN}))  # type: ignore[union-attr]
    assert outcome.possession_proven is True


def test_RL_06_us_decision_makes_no_model_call(db, rulebook, clock, monkeypatch):
    _no_model_calls(monkeypatch)
    order = make_order(
        db, make_seller(db, region="US"), buyer_country="US", currency="USD", amount_minor=2000
    )
    case = make_case(db, order)
    PolicyService(db, rulebook, clock).decide(case, engine_result(), facts_for(order))


# ---------------------------------------------------------------- RL-07

ORDER_ADDRESS = Address(
    name="Sam", line1="Flat 2, 10 High Street", city="London", postal_code="N1 9GU", country="GB"
)


def test_RL_07_redirected_delivery_address_flagged(rulebook):
    redirected = ORDER_ADDRESS.model_copy(
        update={"line1": "Unit 9, Depot Road", "postal_code": "E14 5AB"}
    )
    assert rulebook.seller_protection.is_redirected(ORDER_ADDRESS, redirected) is True


def test_RL_07_same_address_with_formatting_differences_not_flagged(rulebook):
    same = Address(
        name="SAM",
        line1="  flat 2,  10 high street ",
        city="LONDON",
        postal_code="n19gu",
        country="gb",
    )
    assert rulebook.seller_protection.is_redirected(ORDER_ADDRESS, same) is False


# ---------------------------------------------------------------- RL-08


def test_RL_08_decision_stores_rule_id_and_version(db, rulebook, clock):
    order = make_order(db, make_seller(db), amount_minor=3000)
    case = make_case(db, order)
    result = PolicyService(db, rulebook, clock).decide(case, engine_result(), facts_for(order))
    stored = db.query(Decision).filter_by(case_id=case.id).one()
    uk = rulebook.select("GB")
    assert stored.rule_id == result.rule_id == "uk_possession_v1"
    assert stored.rule_version == result.rule_version == uk.version  # type: ignore[union-attr]


# ---------------------------------------------------------------- RL-09

RuleCase = Callable[[RuleBook], bool]
RULE_CASES: dict[str, tuple[RuleCase, RuleCase]] = {
    # rule id: (passing case holds, failing case holds)
    "uk_possession_v1": (
        lambda b: b.select("GB").evaluate(frozenset({P.LOCKER_PICKUP_CODE})).possession_proven,  # type: ignore[union-attr]
        lambda b: not b.select("GB").evaluate(frozenset()).possession_proven,  # type: ignore[union-attr]
    ),
    "eu_possession_v1": (
        lambda b: b.select("DE").evaluate(frozenset({P.SIGNATURE})).possession_proven,  # type: ignore[union-attr]
        lambda b: not b.select("DE").evaluate(frozenset({P.DELIVERED_SCAN})).possession_proven,  # type: ignore[union-attr]
    ),
    "us_seller_policy_v1": (
        lambda b: (
            b.select("US")
            .evaluate(frozenset({P.DELIVERED_SCAN, P.DELIVERY_PHOTO}))
            .possession_proven
        ),  # type: ignore[union-attr]
        lambda b: not b.select("US").evaluate(frozenset({P.DELIVERY_PHOTO})).possession_proven,  # type: ignore[union-attr]
    ),
    "paypal_sp_v1": (
        lambda b: b.seller_protection.signature_recommended(money(100_000, "USD")),
        lambda b: not b.seller_protection.is_redirected(ORDER_ADDRESS, ORDER_ADDRESS),
    ),
    "money_limits_v1": (
        lambda b: b.money_limits.auto_approve_limit(Currency.GBP).amount_minor > 0,
        lambda b: b.money_limits.repeat_claims_threshold > 1,
    ),
}


def test_RL_09_every_rule_file_has_a_case_pair(rulebook):
    assert sorted(rulebook.rule_ids) == sorted(RULE_CASES)


@pytest.mark.parametrize("rule_id", sorted(RULE_CASES))
def test_RL_09_every_rule_file_has_passing_and_failing_case(rulebook, rule_id):
    passing, failing = RULE_CASES[rule_id]
    assert passing(rulebook), f"{rule_id}: passing case"
    assert failing(rulebook), f"{rule_id}: failing case"


# ---------------------------------------------------------------- RL-10


@pytest.mark.parametrize(("minor", "expected"), [(75_001, True), (75_000, False), (10_000, False)])
def test_RL_10_signature_recommended_over_threshold(rulebook, minor, expected):
    assert rulebook.seller_protection.signature_recommended(money(minor, "USD")) is expected


# ---------------------------------------------------------------- RL-11


def test_RL_11_money_limit_defaults(rulebook):
    limits = rulebook.money_limits
    assert limits.auto_approve_limit(Currency.GBP) == money(5000, "GBP")
    assert limits.auto_approve_limit(Currency.USD) == money(5000, "USD")
    assert limits.daily_cap(Currency.GBP) == money(50_000, "GBP")
    assert limits.daily_cap(Currency.USD) == money(50_000, "USD")
    assert limits.repeat_claims_threshold == 3
    assert limits.repeat_claims_window_days == 90


def test_RL_11_money_limits_read_from_yaml(rules_copy):
    # Structure documented in rules/money_limits_v1.yaml (ADR-006).
    _edit(
        rules_copy / "money_limits_v1.yaml",
        lambda d: d["auto_approve_limit_minor"].update(GBP=1234),
    )
    assert load_rules(rules_copy).money_limits.auto_approve_limit(Currency.GBP) == money(1234)


def test_RL_11_seller_override_replaces_default_per_currency(db, rulebook):
    seller = make_seller(db, auto_approve_limits={"GBP": 2000}, daily_refund_caps={"USD": 9000})
    gbp = effective_limits(rulebook, seller, Currency.GBP)
    usd = effective_limits(rulebook, seller, Currency.USD)
    assert gbp.auto_approve == money(2000, "GBP")
    assert gbp.daily_cap == money(50_000, "GBP")
    assert usd.auto_approve == money(5000, "USD")
    assert usd.daily_cap == money(9000, "USD")


def test_RL_11_engine_config_contains_no_money_limits():
    source = (ROOT / "tuning" / "engine_config.py").read_text().lower()
    for word in ("auto_approve", "daily_cap", "refund_cap", "repeat_claim"):
        assert word not in source


def test_RL_11_rule_files_present():
    assert sorted(p.name for p in RULES_DIR.glob("*.yaml")) == sorted(RULE_FILES)
