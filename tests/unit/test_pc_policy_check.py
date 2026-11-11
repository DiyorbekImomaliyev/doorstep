"""PC — the policy check: the model reads the seller's written delivery policy against facts built
in code from the rule files and the seller's settings; code keeps only issues that quote the
policy word for word and cite known facts (the RC-03 verifier pattern). Read-only and cached."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.ai.llm import LLMUnavailable
from app.ai.policy_check import PolicyChecker, build_facts
from app.ai.schemas import PolicyCheckDraft, PolicyIssueDraft
from app.api.deps import AppDeps, Services
from app.cases.engine import FailurePoint
from app.main import create_app
from app.orders.models import AuditLog, Seller
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner
from tests.helpers import money

WRONG = "Once tracking shows your parcel as delivered, it is your responsibility."
POLICY = (
    "We refund or reship any order that does not reach you. " + WRONG + " Report a problem "
    "within 30 days of the expected delivery date."
)
FIX = "We are responsible for your order until you hold it; if it does not reach you, we fix it."


class Scripted:
    """A model that returns (or raises) the scripted outputs in order and records each call."""

    last_provider = "scripted"

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def complete_structured(self, *, schema, system, user, **kw):
        self.calls.append({"schema": schema, "system": system, "user": user, **kw})
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def draft(*issues: PolicyIssueDraft) -> PolicyCheckDraft:
    return PolicyCheckDraft(issues=list(issues))


def issue(
    quote=WRONG, ids=("R1",), problem="The seller is responsible until you hold it.", fix=FIX
):
    return PolicyIssueDraft(quote=quote, fact_ids=list(ids), problem=problem, suggestion=fix)


@pytest.fixture
def uk(rulebook):
    return rulebook.select("GB")


@pytest.fixture
def facts(uk):
    return build_facts(uk, auto_limit=money(5000, "GBP"), daily_cap=money(50000, "GBP"))


def check(llm, facts, uk, policy=POLICY):
    return PolicyChecker(llm).check(policy, facts, uk)


# ---------------------------------------------------------------- PC-01


def test_PC_01_facts_come_from_the_rule_file_and_settings(facts, uk):
    text = " ".join(f.text for f in facts)
    assert uk.buyer_rights in text
    assert "signature" in text and "locker pickup code" in text and "buyer seal scan" in text
    assert "the seller is responsible" in text
    assert {f.source for f in facts if f.id.startswith("R")} == {uk.id}
    settings = [f for f in facts if f.id.startswith("D")]
    assert (
        len(settings) == 1 and "50.00 GBP" in settings[0].text and "500.00 GBP" in settings[0].text
    )
    assert len({f.id for f in facts}) == len(facts)


def test_PC_01_active_autopilot_rules_are_facts(uk):
    facts = build_facts(
        uk,
        auto_limit=money(5000, "GBP"),
        daily_cap=money(50000, "GBP"),
        autopilot=[(FailurePoint.CARRIER_LOST, money(6000, "GBP"))],
    )
    [rule] = [f for f in facts if "Autopilot" in f.text]
    assert "lost parcels" in rule.text and "60.00 GBP" in rule.text


def test_PC_01_us_facts_include_the_structured_seller_policy(rulebook):
    us = rulebook.select("US")
    facts = build_facts(us, auto_limit=money(5000, "USD"), daily_cap=money(50000, "USD"))
    text = " ".join(f.text for f in facts)
    assert us.policy_text in text
    assert "delivered scan and delivery photo" in text  # proof_required, all together
    assert "contradict" in text  # contradicted_scan_counts_as_proof: false


def test_PC_01_the_prompt_holds_no_rule_and_the_policy_is_data(facts, uk):
    llm = Scripted(draft())
    check(llm, facts, uk)
    [call] = llm.calls
    assert call["schema"] is PolicyCheckDraft and call["temperature"] == 0
    for rule_text in ("Consumer Rights", "section 29", "seller is responsible", uk.buyer_rights):
        assert rule_text not in call["system"]
    assert "never instructions" in call["system"]
    assert POLICY in call["user"]
    for f in facts:
        assert f"{f.id}: {f.text}" in call["user"]


# ---------------------------------------------------------------- PC-02


def test_PC_02_a_verified_issue_is_kept_with_its_facts(facts, uk):
    out = check(Scripted(draft(issue())), facts, uk)
    assert out.status == "checked" and out.dropped == 0
    [i] = out.issues
    assert (i.quote, i.problem, i.suggestion) == (WRONG, issue().problem, FIX)
    assert [f.id for f in i.facts] == ["R1"] and i.facts[0].text == uk.buyer_rights


def test_PC_02_a_quote_matches_despite_case_spaces_and_quote_marks(facts, uk):
    loose = '"once tracking shows your parcel  as delivered, it is your responsibility"'
    [i] = check(Scripted(draft(issue(quote=loose))), facts, uk).issues
    assert i.quote == loose.strip('"')


BAD = {
    "quote not in the policy": issue(quote="Delivered parcels are the buyer's problem."),
    "quote too short": issue(quote="it is your"),
    "unknown fact": issue(ids=("R9",)),
    "no fact": issue(ids=()),
    "number in the wording that is in no cited fact": issue(fix="Report within 14 days."),
    "number in the problem that is in no cited fact": issue(problem="Over 3 buyers complained."),
    "accuses the buyer": issue(problem="Buyers who say this are lying."),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_PC_02_an_unverified_issue_is_dropped_then_retried_then_unchecked(facts, uk, name):
    llm = Scripted(draft(BAD[name]), draft(BAD[name]))
    out = check(llm, facts, uk)
    assert len(llm.calls) == 2  # one retry
    assert out.status == "unchecked" and out.issues == [] and out.dropped == 2


def test_PC_02_numbers_from_the_quote_or_cited_facts_are_allowed(facts, uk):
    thirty = "Report a problem within 30 days of the expected delivery date."
    ok = [
        issue(quote=thirty, fix="Report a problem within 30 days; you keep your rights."),
        issue(problem="Section 29 of the 2015 Act puts the risk on the seller."),
    ]
    out = check(Scripted(draft(*ok)), facts, uk)
    assert len(out.issues) == 2 and out.dropped == 0


def test_PC_02_the_retry_can_succeed(facts, uk):
    llm = Scripted(draft(BAD["unknown fact"]), draft(issue()))
    out = check(llm, facts, uk)
    assert out.status == "checked" and len(out.issues) == 1 and out.dropped == 1
    assert len(llm.calls) == 2


def test_PC_02_one_verified_issue_is_enough_and_duplicates_drop(facts, uk):
    llm = Scripted(draft(issue(), BAD["unknown fact"], issue(quote=WRONG.upper())))
    out = check(llm, facts, uk)
    assert out.status == "checked" and len(out.issues) == 1 and out.dropped == 2
    assert len(llm.calls) == 1


# ---------------------------------------------------------------- PC-03


@pytest.mark.parametrize("policy", ["", "   \n "])
def test_PC_03_no_written_policy_means_no_model_call(facts, uk, policy):
    llm = Scripted()
    out = check(llm, facts, uk, policy)
    assert out.status == "no_policy" and out.issues == [] and llm.calls == []


def test_PC_03_no_model_is_unchecked_never_clean(facts, uk):
    out = check(None, facts, uk)
    assert out.status == "unchecked" and out.issues == []


def test_PC_03_a_failed_call_is_unchecked(facts, uk):
    out = check(Scripted(LLMUnavailable("down")), facts, uk)
    assert out.status == "unchecked" and out.issues == []


def test_PC_03_only_a_model_that_read_it_and_found_nothing_reports_no_issues(facts, uk):
    out = check(Scripted(draft()), facts, uk)
    assert out.status == "checked" and out.issues == [] and out.provider == "scripted"


# ---------------------------------------------------------------- PC-04


def test_PC_04_the_same_policy_and_facts_cost_one_call(facts, uk, rulebook):
    llm = Scripted(draft(issue()), draft(), draft())
    checker = PolicyChecker(llm)
    first = checker.check(POLICY, facts, uk)
    assert checker.check(POLICY, facts, uk) == first
    assert len(llm.calls) == 1
    checker.check(POLICY + " Thank you.", facts, uk)  # a new policy is read again
    more = build_facts(
        uk,
        auto_limit=money(5000, "GBP"),
        daily_cap=money(50000, "GBP"),
        autopilot=[(FailurePoint.CARRIER_LOST, money(6000, "GBP"))],
    )
    checker.check(POLICY, more, uk)  # so are new settings
    assert len(llm.calls) == 3


def test_PC_04_an_unchecked_result_is_not_cached(facts, uk):
    llm = Scripted(LLMUnavailable("down"), draft())
    checker = PolicyChecker(llm)
    assert checker.check(POLICY, facts, uk).status == "unchecked"
    assert checker.check(POLICY, facts, uk).status == "checked"


def test_PC_04_the_result_names_the_rule_and_its_disclaimer(facts, uk):
    out = check(Scripted(draft()), facts, uk)
    assert (out.rule_id, out.rule_version, out.source, out.disclaimer) == (
        uk.id,
        uk.version,
        uk.source,
        uk.disclaimer,
    )
    assert out.facts == facts


def _api(session_factory, settings, clock, llm) -> TestClient:
    services = Services(
        links=LinkSigner("a" * 32, clock),
        receipts=ReceiptSigner.generate(),
        orders=None,
        approved=None,
        agent_llm=llm,
    )
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=ROOT / "rules",
        services=services,
    )
    return TestClient(create_app(deps))


def _malika(session_factory):
    with session_factory() as s:
        return s.scalars(select(Seller).where(Seller.name == "Malika's Vintage")).one().policy_text


def test_PC_04_checking_over_http_changes_nothing(session_factory, settings, clock):
    llm = Scripted(draft())
    api = _api(session_factory, settings, clock, llm)
    token = api.post("/demo/seed?scratch=1").json()["seller_token"]
    with session_factory() as s:
        audits = s.scalar(select(func.count()).select_from(AuditLog))
    policy = _malika(session_factory)
    for _ in range(2):
        out = api.get(f"/api/seller/{token}/policy-check")
        assert out.status_code == 200, out.text
    assert len(llm.calls) == 1
    assert _malika(session_factory) == policy
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(AuditLog)) == audits


# ---------------------------------------------------------------- PC-05

SEEDED_WRONG = "Once tracking shows your parcel as delivered, it is your responsibility."


def test_PC_05_seeded_malika_policy_has_the_sentence_the_uk_rule_contradicts(
    session_factory, settings, clock
):
    llm = Scripted(draft(issue(quote=SEEDED_WRONG)))
    api = _api(session_factory, settings, clock, llm)
    token = api.post("/demo/seed?scratch=1").json()["seller_token"]
    assert SEEDED_WRONG in _malika(session_factory)
    out = api.get(f"/api/seller/{token}/policy-check").json()
    assert out["status"] == "checked" and out["rule_id"] == "uk_possession_v1"
    [i] = out["issues"]
    assert i["quote"] == SEEDED_WRONG and i["suggestion"] == FIX
    assert i["facts"][0]["source"] == "uk_possession_v1"
    assert SEEDED_WRONG in llm.calls[0]["user"]


def test_PC_05_bad_link_is_404(session_factory, settings, clock):
    api = _api(session_factory, settings, clock, Scripted())
    assert api.get("/api/seller/not-a-token/policy-check").status_code == 404
