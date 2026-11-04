"""HTTP surface of the buyer and seller journey: signed links, the public receipt check, healthz,
the demo seed and reset (BC-01, BC-04, PR-02, PR-05..08, E2E-02)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api.deps import AppDeps, Services
from app.demo.seed import DEMO_TAG, seed_scenario
from app.main import create_app
from app.orders.enums import MoneyActionStatus
from app.orders.models import MoneyAction, Order
from app.proof.models import Receipt
from app.proof.receipts import ReceiptSigner
from app.security.links import LinkSigner
from tests.conftest import sqlite_session_factory
from tests.fakes import FakeVerifier
from tests.fakes_journey import SAM_FIXED, SAM_NO_FLAT, RecordingPayPal

RULES_DIR = Path(__file__).resolve().parents[2] / "rules"
SECRET = "test-signing-secret-0123456789"


@pytest.fixture
def paypal() -> RecordingPayPal:
    return RecordingPayPal()


@pytest.fixture
def services(clock, paypal) -> Services:
    return Services(
        links=LinkSigner(SECRET, clock),
        receipts=ReceiptSigner.generate(),
        orders=paypal,
        approved=paypal,
    )


def _client(session_factory, settings, clock, services) -> TestClient:
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=FakeVerifier(),
        clock=clock,
        rules_dir=RULES_DIR,
        services=services,
    )
    return TestClient(create_app(deps))


@pytest.fixture
def api(session_factory, settings, clock, services) -> TestClient:
    return _client(session_factory, settings, clock, services)


@pytest.fixture
def demo(api) -> dict:
    response = api.post("/demo/seed?scratch=0")
    assert response.status_code == 200
    return response.json()


def _by_key(demo: dict, key: str) -> dict:
    return next(o for o in demo["summary"]["orders"] if o["key"] == key)


# ---------------------------------------------------------------- BC-01 / BC-04 signed links


def test_BC_01_buyer_page_needs_a_signed_link(api):
    assert api.get("/api/buyer/not-a-token").status_code == 404


def test_BC_01_buyer_page_opens_with_its_link(api, demo):
    view = api.get(f"/api/buyer/{demo['buyers']['sam-jacket']}").json()
    assert view["item"] == "Black jacket, size M"
    assert view["status"] == "Marked as delivered"
    assert view["can_report"] is True


def test_BC_04_seller_link_is_not_a_buyer_link(api, demo):
    assert api.get(f"/api/buyer/{demo['seller_token']}").status_code == 404
    assert api.get(f"/api/seller/{demo['buyers']['sam-jacket']}/orders").status_code == 404


def test_BC_04_a_seller_cannot_open_another_sellers_case(api, demo):
    other = api.post("/demo/seed?scratch=1").json()
    held = _by_key(demo, "empty-box")["case_id"]
    assert api.get(f"/api/seller/{other['seller_token']}/cases/{held}").status_code == 404
    response = api.post(f"/api/seller/{other['seller_token']}/cases/{held}/approve")
    assert response.status_code == 404


def test_BC_04_buyer_view_never_shows_the_full_address_or_packing_internals(api, demo):
    view = api.get(f"/api/buyer/{demo['buyers']['sam-jacket']}").json()
    text = str(view)
    assert "Demo Street" not in text
    assert all(item["title"] != "packing photo" for item in view["timeline"])


# ---------------------------------------------------------------- BC-02 / BC-03 over HTTP


def test_BC_02_got_it_over_http(api, demo):
    view = api.post(f"/api/buyer/{demo['buyers']['sam-jacket']}/got-it").json()
    assert view["state"] == "CONFIRMED"
    assert view["can_report"] is True  # a buyer can still report after confirming


def test_BC_03_not_here_over_http_returns_a_plain_message(api, demo):
    response = api.post(
        f"/api/buyer/{demo['buyers']['sam-jacket']}/not-here", json={"reason": "not_received"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "AWAITING_SELLER"  # £60 is over the £50 auto-approve limit
    assert "seller" in body["message"].lower()
    again = api.post(f"/api/buyer/{demo['buyers']['sam-jacket']}/not-here", json={})
    assert again.status_code == 409


def test_seller_approves_with_one_tap_and_gets_a_signed_receipt(api, demo, session_factory):
    token = demo["buyers"]["sam-jacket"]
    case_id = api.post(f"/api/buyer/{token}/not-here", json={}).json()["case_id"]
    page = api.get(f"/api/seller/{demo['seller_token']}/cases/{case_id}").json()
    assert page["actions"] == ["approve", "decline"]
    assert page["decision"]["rule_id"] == "uk_possession_v1"
    assert "not legal advice" in page["disclaimer"]
    out = api.post(f"/api/seller/{demo['seller_token']}/cases/{case_id}/approve").json()
    assert out["money_action_id"]
    with session_factory() as s:
        ma = s.get(MoneyAction, out["money_action_id"])
        assert ma.status == MoneyActionStatus.QUEUED.value and ma.amount_minor == 6000
        kinds = [r.kind for r in s.scalars(select(Receipt).where(Receipt.case_id == case_id))]
    assert kinds == ["decision", "approve"]
    assert api.get(f"/cases/{case_id}/verify").json()["status"] == "ok"


def test_seller_cannot_approve_twice_into_two_refunds(api, demo, session_factory):
    token = demo["buyers"]["sam-jacket"]
    case_id = api.post(f"/api/buyer/{token}/not-here", json={}).json()["case_id"]
    first = api.post(f"/api/seller/{demo['seller_token']}/cases/{case_id}/approve").json()
    second = api.post(f"/api/seller/{demo['seller_token']}/cases/{case_id}/approve")
    assert second.status_code in (200, 409)
    with session_factory() as s:
        refunds = s.scalars(select(MoneyAction).where(MoneyAction.case_id == case_id)).all()
    assert [r.id for r in refunds] == [first["money_action_id"]]


def test_TR_03_failed_attempt_choice_over_http(api, demo):
    token = demo["buyers"]["failed-attempt"]
    assert api.get(f"/api/buyer/{token}").json()["choice_offered"] == ["rebook", "locker"]
    view = api.post(f"/api/buyer/{token}/choice", json={"option": "locker"}).json()
    assert view["choice_made"] == "locker" and view["choice_offered"] == []


# ---------------------------------------------------------------- checkout over HTTP


def test_CK_checkout_over_http_fixes_the_flat_number_before_capture(api, demo, paypal):
    seller_id = demo["summary"]["seller_id"]
    quote = api.post(
        "/api/checkout/quote",
        json={
            "seller_id": seller_id,
            "amount_minor": 6000,
            "currency": "GBP",
            "buyer_country": "GB",
        },
    ).json()
    assert {o["method"] for o in quote["options"]} == {"home", "locker", "signature"}
    started = api.post(
        "/api/checkout/start",
        json={
            "seller_id": seller_id,
            "amount_minor": 6000,
            "currency": "GBP",
            "buyer_country": "GB",
            "method": quote["recommended"],
            "item_description": "Black jacket, size M",
        },
    ).json()
    assert started["approve_url"].startswith("https://")
    paypal.approve(started["paypal_order_id"], SAM_NO_FLAT)
    token = started["buyer_token"]
    check = api.post(f"/api/buyer/{token}/approved").json()
    assert check["valid"] is False and check["issues"][0]["code"] == "missing_unit"
    fixed = api.post(f"/api/buyer/{token}/address", json=SAM_FIXED.model_dump()).json()
    assert fixed["valid"] is True
    assert len(paypal.address_calls) == 1


def test_CK_unknown_seller_is_404(api):
    body = {"seller_id": "nope", "amount_minor": 100, "currency": "GBP", "buyer_country": "GB"}
    assert api.post("/api/checkout/quote", json=body).status_code == 404


def test_PO_10_unsupported_currency_is_rejected_at_checkout(api, demo):
    body = {
        "seller_id": demo["summary"]["seller_id"],
        "amount_minor": 100,
        "currency": "EUR",
        "buyer_country": "GB",
    }
    assert api.post("/api/checkout/quote", json=body).status_code == 422


# ---------------------------------------------------------------- Control Room


def test_control_room_rows_mask_addresses_and_flag_actions(api, demo):
    rows = api.get(f"/api/seller/{demo['seller_token']}/orders").json()
    assert len(rows) == len(demo["summary"]["orders"])
    sam = next(r for r in rows if r["item"] == "Black jacket, size M")
    assert sam["buyer_area"] == "N1"
    assert "Demo Street" not in str(rows)
    lost = next(r for r in rows if r["item"] == "Denim jacket, size S")
    assert lost["needs_action"] is True and lost["route"] == "HOLD_FOR_REVIEW"  # low confidence
    assert all(0.0 <= r["evidence"]["score"] <= 1.0 for r in rows)
    assert all(r["simulated"] for r in rows)


# ---------------------------------------------------------------- PR-02 / PR-08 verify


def test_PR_02_public_verify_reports_the_first_tampered_receipt(api, demo, session_factory):
    case_id = _by_key(demo, "empty-box")["case_id"]
    assert api.get(f"/cases/{case_id}/verify").json()["status"] == "ok"
    with session_factory() as s:
        receipt = s.scalars(select(Receipt).where(Receipt.case_id == case_id)).one()
        s.execute(
            update(Receipt)
            .where(Receipt.id == receipt.id)
            .values(payload={**receipt.payload, "refund_amount_minor": 1})
        )
        s.commit()
        seq = receipt.seq
    report = api.get(f"/cases/{case_id}/verify").json()
    assert report["status"] == "broken" and report["first_broken_seq"] == seq


def test_PR_08_verify_without_receipts_says_not_run(api):
    report = api.get("/cases/no-such-case/verify").json()
    assert report["status"] == "not_run"


def test_PR_08_simulated_refunds_are_labelled_simulated(
    api, demo, session_factory, clock, settings
):
    from app.policy.rules import load_rules
    from app.policy.service import REFUND_JOB, RefundJobHandler
    from app.worker.simulated import RoutingRefundGateway, SimulatedRefundGateway
    from tests.helpers import run_jobs

    sim = SimulatedRefundGateway(session_factory)
    handler = RefundJobHandler(
        RoutingRefundGateway(real=None, simulated=sim),  # type: ignore[arg-type]
        load_rules(RULES_DIR),
        clock,
    )
    run_jobs(session_factory, {REFUND_JOB: handler}, clock, settings)
    case_id = _by_key(demo, "damaged-mug")["case_id"]
    page = api.get(f"/api/seller/{demo['seller_token']}/cases/{case_id}").json()
    assert page["state"] == "SETTLED"
    assert [m["status"] for m in page["money"]] == ["simulated"]
    assert page["money"][0]["refund_id"].startswith("SIM-REFUND-")


# ---------------------------------------------------------------- PR-05 healthz


def test_PR_05_healthz_lists_every_simulated_component(api):
    body = api.get("/healthz").json()
    assert {"lockers", "rerouting", "carrier_claims", "scratch_refunds"} <= set(body["simulated"])
    assert "uk_possession_v1" in body["rules"]
    assert body["receipts_public_key"]


# ---------------------------------------------------------------- PR-06 scratch


def test_PR_06_each_scratch_seed_is_isolated_from_the_filmed_demo(api, demo, session_factory):
    a = api.post("/demo/seed?scratch=1").json()
    b = api.post("/demo/seed?scratch=1").json()
    assert len({demo["tag"], a["tag"], b["tag"]}) == 3
    assert a["summary"]["seller_id"] != demo["summary"]["seller_id"]
    # Acting on a scratch copy never changes the filmed demo.
    api.post(f"/api/buyer/{a['buyers']['sam-jacket']}/got-it")
    with session_factory() as s:
        filmed = s.get(Order, _by_key(demo, "sam-jacket")["order_id"])
        assert filmed.state == "DELIVERED"
    rows = api.get(f"/api/seller/{a['seller_token']}/orders").json()
    assert {r["order_id"] for r in rows}.isdisjoint(
        {o["order_id"] for o in demo["summary"]["orders"]}
    )


# ---------------------------------------------------------------- PR-07 reset


def test_PR_07_reset_needs_the_exact_phrase(api, demo):
    assert api.post("/demo/reset", json={"confirm": "yes"}).status_code == 400
    response = api.post("/demo/reset", json={"confirm": "RESET DEMO DATA"})
    assert response.status_code == 200
    assert response.json()["seed"]["tag"] == DEMO_TAG


def test_PR_07_reset_is_disabled_outside_development(session_factory, settings, clock, services):
    services.environment = "production"
    api = _client(session_factory, settings, clock, services)
    response = api.post("/demo/reset", json={"confirm": "RESET DEMO DATA"})
    assert response.status_code == 403


# ---------------------------------------------------------------- E2E-02 deterministic seed


def _snapshot(clock, settings, rulebook) -> list[tuple]:
    factory = sqlite_session_factory()
    with factory() as s:
        summary = seed_scenario(
            s, rulebook=rulebook, clock=clock, settings=settings, signer=ReceiptSigner.generate()
        )
        return [(o.key, o.order_id, o.state, o.route) for o in summary.orders]


def test_E2E_02_demo_seed_is_identical_every_time(clock, settings, rulebook):
    first = _snapshot(clock, settings, rulebook)
    second = _snapshot(clock, settings, rulebook)
    assert first == second
    routes = {key: route for key, _, _, route in first}
    assert routes["empty-box"] == "HOLD_FOR_REVIEW"
    assert routes["damaged-mug"] == "AUTO_REFUND"
    assert routes["lost-denim"] == "HOLD_FOR_REVIEW"  # stale only: low confidence


def test_E2E_02_seeding_the_same_tag_twice_changes_nothing(db, clock, settings, rulebook):
    signer = ReceiptSigner.generate()
    a = seed_scenario(db, rulebook=rulebook, clock=clock, settings=settings, signer=signer)
    b = seed_scenario(db, rulebook=rulebook, clock=clock, settings=settings, signer=signer)
    assert a == b
