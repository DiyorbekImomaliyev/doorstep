"""PK over HTTP: the seller sends one photo; the answer is match, mismatch (with the attribute)
or check. Only the seller who owns the order can do it."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.ai.llm import LLMAdapter
from app.ai.packing import PackingChecker
from app.ai.schemas import PackingCheckResult
from app.api.deps import AppDeps, Services
from app.main import create_app
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner

JPEG = base64.b64encode(b"\xff\xd8fake-jpeg-bytes").decode()


class Always:
    name = "always"

    def __init__(self, result: PackingCheckResult) -> None:
        self.result = result

    def generate(self, schema, **kw):
        return self.result


def _result(matches: bool, colour: str, attribute: str = "none") -> PackingCheckResult:
    return PackingCheckResult(
        matches=matches,
        item="jacket",
        colour=colour,
        size_seen="M",
        reasons=[f"{colour} jacket"],
        confidence=0.95,
        mismatch_attribute=attribute,
    )


def _api(session_factory, settings, clock, result) -> TestClient:
    services = Services(
        links=LinkSigner("x" * 32, clock),
        receipts=ReceiptSigner.generate(),
        orders=None,
        approved=None,
        packing=PackingChecker(LLMAdapter(Always(result))) if result else None,
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


def _paid_order(api: TestClient) -> tuple[str, str]:
    """A fresh seller with one paid, unshipped order, via checkout + approval in the seed."""
    seed = api.post("/demo/seed?scratch=1").json()
    token = seed["seller_token"]
    rows = api.get(f"/api/seller/{token}/orders").json()
    paid = [r for r in rows if r["state"] in ("PAID", "PACKED")]
    if not paid:
        pytest.skip("scenario has no paid order")
    return token, paid[0]["order_id"]


@pytest.fixture
def mismatch_api(session_factory, settings, clock):
    return _api(session_factory, settings, clock, _result(False, "blue", "colour"))


def test_PK_03_mismatch_over_http_names_the_colour(mismatch_api):
    token, order_id = _paid_order(mismatch_api)
    r = mismatch_api.post(
        f"/api/seller/{token}/orders/{order_id}/packing-check",
        json={"image_b64": JPEG, "media_type": "image/jpeg"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["verdict"], body["attribute"]) == ("mismatch", "colour")
    assert body["order"]["packing_verdict"] == "mismatch"
    cleared = mismatch_api.post(f"/api/seller/{token}/orders/{order_id}/packing-clear")
    assert cleared.status_code == 200
    assert cleared.json()["state"] == "PACKED"
    assert cleared.json()["packing_cleared"] is True


def test_PK_04_no_model_configured_answers_check(session_factory, settings, clock):
    api = _api(session_factory, settings, clock, None)
    token, order_id = _paid_order(api)
    r = api.post(f"/api/seller/{token}/orders/{order_id}/packing-check", json={"image_b64": JPEG})
    assert r.json()["verdict"] == "check"


def test_PK_01_rejects_non_images_and_bad_base64(mismatch_api):
    token, order_id = _paid_order(mismatch_api)
    url = f"/api/seller/{token}/orders/{order_id}/packing-check"
    assert (
        mismatch_api.post(url, json={"image_b64": JPEG, "media_type": "text/html"}).status_code
        == 415
    )
    assert mismatch_api.post(url, json={"image_b64": "not base64!!"}).status_code == 422


def test_PK_01_another_sellers_link_cannot_check_the_order(mismatch_api):
    _, order_id = _paid_order(mismatch_api)
    other = mismatch_api.post("/demo/seed?scratch=1").json()["seller_token"]
    r = mismatch_api.post(
        f"/api/seller/{other}/orders/{order_id}/packing-check", json={"image_b64": JPEG}
    )
    assert r.status_code == 404
