"""PL-10: the Policy page reads the seller's written policy and the rule files its check uses."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.automate_desk import rule_file
from app.api.deps import AppDeps, Services
from app.main import create_app
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner
from tests.factories import make_order, make_seller

SECRET = "policy-desk-secret-0123456789abc"
POLICY = "Once tracking shows your parcel as delivered, it is your responsibility."


@pytest.fixture
def api(session_factory, settings, clock) -> TestClient:
    services = Services(
        links=LinkSigner(SECRET, clock),
        receipts=ReceiptSigner.generate(),
        orders=None,
        approved=None,
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


def _get(api, clock, owner_id, scope="seller"):
    token = LinkSigner(SECRET, clock).issue(owner_id, scope=scope)
    return api.get(f"/api/seller/{token}/automate/policy")


def test_PL_10_policy_text_and_the_rule_files_by_id_version_and_source(db, clock, api, rulebook):
    seller = make_seller(db)
    seller.policy_text = POLICY
    db.commit()
    body = _get(api, clock, seller.id).json()
    assert body["policy_text"] == POLICY and body["region"] == "GB"
    law, limits = body["rules"]
    uk = rulebook.select("GB")
    assert (law["id"], law["version"], law["source"]) == (uk.id, uk.version, uk.source)
    assert law["title"] == uk.source.split(" — ")[0]  # the law's name, without its link
    assert law["url"] and law["url"].startswith("https://") and law["url"] in uk.source
    assert (limits["id"], limits["version"]) == (
        rulebook.money_limits.id,
        rulebook.money_limits.version,
    )
    assert limits["url"] is None and limits["title"] == rulebook.money_limits.source
    assert body["own_limits"] is False


def test_PL_10_no_policy_own_limits_and_a_region_without_a_rule(db, clock, api, rulebook):
    seller = make_seller(db, region="JP", daily_refund_caps={"GBP": 1000})
    db.commit()
    body = _get(api, clock, seller.id).json()
    assert body["policy_text"] == ""
    assert [r["id"] for r in body["rules"]] == [rulebook.money_limits.id]
    assert body["own_limits"] is True


def test_PL_10_every_rule_file_has_a_title(rulebook):
    for rule in [*rulebook.liability, rulebook.money_limits]:
        f = rule_file(rule)
        assert f.title and "http" not in f.title and f.version == rule.version


def test_PL_10_seller_link_only(db, clock, api):
    seller = make_seller(db)
    order = make_order(db, seller)
    db.commit()
    assert api.get("/api/seller/not-a-token/automate/policy").status_code == 404
    assert _get(api, clock, order.id, scope="buyer").status_code == 404
    assert _get(api, clock, "no-such-seller").status_code == 404
