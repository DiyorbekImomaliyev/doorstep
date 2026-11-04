"""PP — PayPal client. HTTP-level tests against a local fake server (pytest-httpserver):
transport-agnostic, no network beyond localhost."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import pytest
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from app.money import Money
from app.orders.enums import CaseState, ShippingMethod
from app.orders.schemas import Address
from app.paypal_client import reads
from app.paypal_client.client import PayPalClient, PayPalConfig
from app.paypal_client.errors import (
    PayPalAuthError,
    PayPalClientError,
    PayPalServerError,
    PayPalTimeout,
)
from app.paypal_client.idempotency import PAYPAL_REQUEST_ID_MAX_LEN, request_id_for
from app.paypal_client.types import DisputeEvidence, ShippingOption
from app.paypal_client.writes import money as money_writes
from app.paypal_client.writes import orders as order_writes
from app.policy.gateway import RestRefundGateway
from app.policy.service import REFUND_JOB, PolicyService, RefundJobHandler
from app.worker.gateway import RestOrdersGateway
from app.worker.handlers import ADD_TRACKING, AddTrackingHandler
from app.worker.runner import JobRunner, enqueue
from tests.factories import make_case, make_order, make_seller, make_shipment
from tests.fakes import FakeClock
from tests.helpers import engine_result, facts_for, money

SECRET = "SECRET-do-not-log-9f2c"
GBP60 = Money(amount_minor=6000, currency="GBP")
OPTIONS = [
    ShippingOption(method=ShippingMethod.HOME, label="Home", amount=money(0)),
    ShippingOption(method=ShippingMethod.LOCKER, label="Locker", amount=money(0)),
    ShippingOption(method=ShippingMethod.SIGNATURE, label="Signature", amount=money(250)),
]
ORDER_CREATED = {
    "id": "O-1",
    "status": "CREATED",
    "links": [
        {"rel": "self", "href": "https://api.sandbox.paypal.test/v2/checkout/orders/O-1"},
        {"rel": "approve", "href": "https://www.sandbox.paypal.test/checkoutnow?token=O-1"},
    ],
}


def capture_body(status: str = "COMPLETED") -> dict[str, Any]:
    return {
        "id": "O-1",
        "status": "COMPLETED" if status == "COMPLETED" else status,
        "purchase_units": [
            {
                "payments": {
                    "captures": [
                        {
                            "id": "CAP-1",
                            "status": status,
                            "amount": {"currency_code": "GBP", "value": "60.00"},
                        }
                    ]
                }
            }
        ],
    }


@pytest.fixture
def pp(httpserver: HTTPServer, clock: FakeClock) -> PayPalClient:
    config = PayPalConfig(
        client_id="client-id",
        client_secret=SECRET,
        base_url=httpserver.url_for("").rstrip("/"),
        webhook_id="WH-ID",
        timeout_s=0.5,
        token_refresh_margin_s=300,
    )
    return PayPalClient(config, clock)


def serve_token(httpserver: HTTPServer, token: str = "TOKEN-1", expires_in: int = 32400) -> None:
    httpserver.expect_request("/v1/oauth2/token", method="POST").respond_with_json(
        {"access_token": token, "token_type": "Bearer", "expires_in": expires_in}
    )


def token_requests(httpserver: HTTPServer) -> int:
    return sum(1 for req, _ in httpserver.log if req.path == "/v1/oauth2/token")


def api_requests(httpserver: HTTPServer, path: str) -> list[Request]:
    return [req for req, _ in httpserver.log if req.path == path]


def body_of(req: Request) -> Any:
    return json.loads(req.get_data(as_text=True) or "null")


# ---------------------------------------------------------------- PP-01


def test_PP_01_token_is_reused_until_refresh_margin(pp, httpserver, clock):
    serve_token(httpserver, expires_in=1000)
    httpserver.expect_request("/v2/checkout/orders/O-1", method="GET").respond_with_json(
        {"id": "O-1"}
    )
    reads.get_order(pp, "O-1")
    clock.advance(1000 - 300 - 1)  # still outside the 300 s refresh margin
    reads.get_order(pp, "O-1")
    assert token_requests(httpserver) == 1


def test_PP_01_token_refreshed_inside_refresh_margin(pp, httpserver, clock):
    serve_token(httpserver, expires_in=1000)
    httpserver.expect_request("/v2/checkout/orders/O-1", method="GET").respond_with_json(
        {"id": "O-1"}
    )
    reads.get_order(pp, "O-1")
    clock.advance(1000 - 300 + 1)  # inside the refresh margin
    reads.get_order(pp, "O-1")
    assert token_requests(httpserver) == 2


def test_PP_01_401_refreshes_token_and_retries_once(pp, httpserver):
    httpserver.expect_ordered_request("/v1/oauth2/token", method="POST").respond_with_json(
        {"access_token": "TOKEN-1", "expires_in": 32400}
    )
    httpserver.expect_ordered_request("/v2/checkout/orders/O-1", method="GET").respond_with_json(
        {"name": "AUTHENTICATION_FAILURE"}, status=401
    )
    httpserver.expect_ordered_request("/v1/oauth2/token", method="POST").respond_with_json(
        {"access_token": "TOKEN-2", "expires_in": 32400}
    )
    httpserver.expect_ordered_request("/v2/checkout/orders/O-1", method="GET").respond_with_json(
        {"id": "O-1"}
    )
    assert reads.get_order(pp, "O-1")["id"] == "O-1"
    calls = api_requests(httpserver, "/v2/checkout/orders/O-1")
    assert len(calls) == 2
    assert calls[1].headers["Authorization"] == "Bearer TOKEN-2"


def test_PP_01_second_401_raises_auth_error_without_loop(pp, httpserver):
    serve_token(httpserver)
    httpserver.expect_request("/v2/checkout/orders/O-1", method="GET").respond_with_json(
        {"name": "AUTHENTICATION_FAILURE"}, status=401
    )
    with pytest.raises(PayPalAuthError):
        reads.get_order(pp, "O-1")
    assert len(api_requests(httpserver, "/v2/checkout/orders/O-1")) == 2


# ---------------------------------------------------------------- PP-02


def _create(
    pp: PayPalClient, httpserver: HTTPServer, selected: ShippingMethod, amount: Money = GBP60
) -> Any:
    serve_token(httpserver)
    httpserver.expect_request("/v2/checkout/orders", method="POST").respond_with_json(
        ORDER_CREATED, status=201
    )
    created = order_writes.create_order(
        pp, amount=amount, options=OPTIONS, selected=selected, request_id="order:o1:create"
    )
    return created, body_of(api_requests(httpserver, "/v2/checkout/orders")[-1])


def test_PP_02_create_order_sends_capture_intent_amount_and_currency(pp, httpserver):
    _, body = _create(pp, httpserver, ShippingMethod.HOME)
    assert body["intent"] == "CAPTURE"
    unit = body["purchase_units"][0]
    assert unit["amount"]["currency_code"] == "GBP"
    assert unit["amount"]["value"] == "60.00"


@pytest.mark.parametrize("selected", list(ShippingMethod))
def test_PP_02_create_order_sends_three_shipping_options_with_one_selected(
    pp, httpserver, selected
):
    _, body = _create(pp, httpserver, selected)
    options = body["purchase_units"][0]["shipping"]["options"]
    assert {o["id"] for o in options} == {"home", "locker", "signature"}
    chosen = [o for o in options if o["selected"] is True]
    assert len(chosen) == 1
    assert chosen[0]["id"] == selected.value
    for option in options:
        assert isinstance(option["amount"]["value"], str)


@pytest.mark.parametrize(
    ("minor", "expected"), [(1999, "19.99"), (5, "0.05"), (100000, "1000.00"), (10, "0.10")]
)
def test_PP_02_amount_is_exact_decimal_string_from_minor_units(pp, httpserver, minor, expected):
    _, body = _create(pp, httpserver, ShippingMethod.HOME, amount=money(minor))
    value = body["purchase_units"][0]["amount"]["value"]
    assert isinstance(value, str)
    assert value == expected


def test_PP_02_create_order_returns_order_id_and_approve_link(pp, httpserver):
    created, _ = _create(pp, httpserver, ShippingMethod.LOCKER)
    assert created.order_id == "O-1"
    assert created.approve_url == "https://www.sandbox.paypal.test/checkoutnow?token=O-1"


# ---------------------------------------------------------------- PP-03 (HTTP level)


def test_PP_03_instrument_declined_422_returns_declined_result(pp, httpserver):
    serve_token(httpserver)
    httpserver.expect_request("/v2/checkout/orders/O-1/capture", method="POST").respond_with_json(
        {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "INSTRUMENT_DECLINED"}]},
        status=422,
    )
    result = order_writes.capture_order(pp, "O-1", request_id="order:o1:capture")
    assert result.status == "DECLINED"


def test_PP_03_capture_returns_capture_id_status_and_amount(pp, httpserver):
    serve_token(httpserver)
    httpserver.expect_request("/v2/checkout/orders/O-1/capture", method="POST").respond_with_json(
        capture_body("COMPLETED"), status=201
    )
    result = order_writes.capture_order(pp, "O-1", request_id="order:o1:capture")
    assert result.status == "COMPLETED"
    assert result.capture_id == "CAP-1"
    assert result.amount == GBP60


# ---------------------------------------------------------------- PP-04


def test_PP_04_add_tracking_sends_carrier_and_tracking_number(pp, httpserver):
    serve_token(httpserver)
    httpserver.expect_request("/v2/checkout/orders/O-1/track", method="POST").respond_with_json(
        {"id": "O-1"}, status=201
    )
    order_writes.add_tracking(
        pp,
        paypal_order_id="O-1",
        capture_id="CAP-1",
        carrier="OTHER",
        tracking_number="SHIPPO_TRANSIT",
        request_id="shipment:s1:add_tracking",
    )
    body = body_of(api_requests(httpserver, "/v2/checkout/orders/O-1/track")[-1])
    assert body["capture_id"] == "CAP-1"
    assert body["tracking_number"] == "SHIPPO_TRANSIT"
    assert "carrier" in body


# ---------------------------------------------------------------- PP-05

WriteCall = Callable[[PayPalClient, str], object]
WRITES: list[tuple[str, WriteCall, str, str, Any, int]] = [
    (
        "create_order",
        lambda pp, rid: order_writes.create_order(
            pp, amount=GBP60, options=OPTIONS, selected=ShippingMethod.HOME, request_id=rid
        ),
        "POST",
        "/v2/checkout/orders",
        ORDER_CREATED,
        201,
    ),
    (
        "update_shipping_address",
        lambda pp, rid: order_writes.update_shipping_address(
            pp,
            "O-1",
            Address(
                line1="Flat 2, 10 High Street", city="London", postal_code="N1 9GU", country="GB"
            ),
            request_id=rid,
        ),
        "PATCH",
        "/v2/checkout/orders/O-1",
        "",
        204,
    ),
    (
        "capture_order",
        lambda pp, rid: order_writes.capture_order(pp, "O-1", request_id=rid),
        "POST",
        "/v2/checkout/orders/O-1/capture",
        capture_body(),
        201,
    ),
    (
        "add_tracking",
        lambda pp, rid: order_writes.add_tracking(
            pp,
            paypal_order_id="O-1",
            capture_id="CAP-1",
            carrier="OTHER",
            tracking_number="SHIPPO_TRANSIT",
            request_id=rid,
        ),
        "POST",
        "/v2/checkout/orders/O-1/track",
        {"id": "O-1"},
        201,
    ),
    (
        "refund_capture",
        lambda pp, rid: money_writes.refund_capture(pp, "CAP-1", amount=None, request_id=rid),
        "POST",
        "/v2/payments/captures/CAP-1/refund",
        {"id": "R-1", "status": "COMPLETED"},
        201,
    ),
    (
        "provide_dispute_evidence",
        lambda pp, rid: money_writes.provide_dispute_evidence(
            pp,
            "D-1",
            evidence=DisputeEvidence(
                evidence_type="PROOF_OF_FULFILLMENT",
                notes="Delivered",
                carrier="OTHER",
                tracking_number="SHIPPO_DELIVERED",
            ),
            request_id=rid,
        ),
        "POST",
        "/v1/customer/disputes/D-1/provide-evidence",
        {"links": []},
        200,
    ),
]


@pytest.mark.parametrize(
    ("name", "call", "method", "path", "body", "status"), WRITES, ids=[w[0] for w in WRITES]
)
def test_PP_05_every_write_sends_paypal_request_id(
    pp, httpserver, name, call, method, path, body, status
):
    serve_token(httpserver)
    if body == "":
        httpserver.expect_request(path, method=method).respond_with_data("", status=status)
    else:
        httpserver.expect_request(path, method=method).respond_with_json(body, status=status)
    rid = f"test:{name}:write"
    call(pp, rid)
    sent = api_requests(httpserver, path)
    assert sent, f"{name} sent nothing to {path}"
    assert sent[-1].headers.get("PayPal-Request-Id") == rid


def test_PP_05_request_id_format_and_length_limit():
    assert request_id_for("case", "c1", "refund", "ma1") == "case:c1:refund:ma1"
    assert request_id_for("order", "o1", "capture") == "order:o1:capture"
    assert request_id_for("order", "o1", "capture") == request_id_for("order", "o1", "capture")
    uuid_a = "6f1c1f7e-8f43-4c55-9d1e-0a6b0e5d7c11"
    uuid_b = "0d6b8a2c-3e7f-4f9a-8b1c-2d3e4f5a6b7c"
    assert (
        len(request_id_for("case", uuid_a, "partial_refund", uuid_b)) <= PAYPAL_REQUEST_ID_MAX_LEN
    )
    long_a, long_b = "a" * 300, "a" * 299 + "b"
    key_a = request_id_for("case", long_a, "refund", uuid_b)
    key_b = request_id_for("case", long_b, "refund", uuid_b)
    assert len(key_a) <= PAYPAL_REQUEST_ID_MAX_LEN
    assert key_a != key_b
    assert key_a == request_id_for("case", long_a, "refund", uuid_b)


def test_PP_05_retry_reuses_same_request_id(pp, httpserver, db, session_factory, clock, settings):
    seller = make_seller(db)
    order = make_order(db, seller, paypal_order_id="O-1", capture_id="CAP-1")
    shipment = make_shipment(db, order)
    enqueue(db, ADD_TRACKING, {"shipment_id": shipment.id}, clock=clock)
    db.commit()
    serve_token(httpserver)  # permanent: the OAuth request is not part of the order check
    # Oneshot handlers are used up in registration order: first attempt 503, retry 201.
    httpserver.expect_oneshot_request(
        "/v2/checkout/orders/O-1/track", method="POST"
    ).respond_with_json({"name": "INTERNAL_SERVER_ERROR"}, status=503)
    httpserver.expect_oneshot_request(
        "/v2/checkout/orders/O-1/track", method="POST"
    ).respond_with_json({"id": "O-1"}, status=201)
    runner = JobRunner(
        session_factory,
        {ADD_TRACKING: AddTrackingHandler(RestOrdersGateway(pp), clock)},
        clock,
        settings,
    )
    runner.run_due()
    clock.advance(settings.backoff_base_s)
    runner.run_due()
    sent = api_requests(httpserver, "/v2/checkout/orders/O-1/track")
    assert len(sent) == 2
    keys = {req.headers.get("PayPal-Request-Id") for req in sent}
    assert keys == {request_id_for("shipment", shipment.id, "add_tracking")}


def test_PP_05_two_partial_refunds_get_different_request_ids():
    first = request_id_for("case", "c1", "partial_refund", "ma-1")
    second = request_id_for("case", "c1", "partial_refund", "ma-2")
    assert first != second


# ---------------------------------------------------------------- PP-06


def test_PP_06_calls_logged_with_request_id_status_and_latency(pp, httpserver, caplog):
    caplog.set_level(logging.INFO, logger="doorstep.paypal")
    serve_token(httpserver)
    httpserver.expect_request(
        "/v2/payments/captures/CAP-1/refund", method="POST"
    ).respond_with_json({"id": "R-1", "status": "COMPLETED"}, status=201)
    money_writes.refund_capture(pp, "CAP-1", amount=None, request_id="case:c1:refund:ma1")
    records = [r for r in caplog.records if getattr(r, "request_id", None) == "case:c1:refund:ma1"]
    assert records, "no log record carries the PayPal-Request-Id"
    record = records[-1]
    assert record.status == 201  # type: ignore[attr-defined]
    assert isinstance(record.latency_ms, int | float) and record.latency_ms >= 0  # type: ignore[attr-defined]


def test_PP_06_logs_never_contain_secret_token_or_authorization(pp, httpserver, caplog):
    caplog.set_level(logging.DEBUG)  # every logger, including httpx/httpcore
    serve_token(httpserver, token="TOKEN-SHOULD-NOT-LEAK")
    httpserver.expect_request("/v2/checkout/orders/O-1", method="GET").respond_with_json(
        {"id": "O-1"}
    )
    httpserver.expect_request("/v2/checkout/orders/O-2", method="GET").respond_with_json(
        {"name": "AUTHENTICATION_FAILURE"}, status=401
    )
    reads.get_order(pp, "O-1")
    with pytest.raises(PayPalAuthError):
        reads.get_order(pp, "O-2")
    dumped = caplog.text + "".join(repr(vars(r)) for r in caplog.records)
    for forbidden in (SECRET, "TOKEN-SHOULD-NOT-LEAK", "Authorization", "Bearer "):
        assert forbidden not in dumped


# ---------------------------------------------------------------- PP-07


def test_PP_07_refund_4xx_raises_non_retryable_error(pp, httpserver):
    serve_token(httpserver)
    httpserver.expect_request(
        "/v2/payments/captures/CAP-1/refund", method="POST"
    ).respond_with_json({"name": "UNPROCESSABLE_ENTITY"}, status=422)
    with pytest.raises(PayPalClientError) as err:
        money_writes.refund_capture(pp, "CAP-1", amount=None, request_id="k")
    assert err.value.status == 422
    assert len(api_requests(httpserver, "/v2/payments/captures/CAP-1/refund")) == 1


def test_PP_07_refund_5xx_raises_retryable_error(pp, httpserver):
    serve_token(httpserver)
    httpserver.expect_request(
        "/v2/payments/captures/CAP-1/refund", method="POST"
    ).respond_with_json({"name": "INTERNAL_SERVER_ERROR"}, status=503)
    with pytest.raises(PayPalServerError):
        money_writes.refund_capture(pp, "CAP-1", amount=None, request_id="k")
    assert len(api_requests(httpserver, "/v2/payments/captures/CAP-1/refund")) == 1


def test_PP_07_refund_timeout_raises_retryable_error(pp, httpserver):
    serve_token(httpserver)

    def slow(_: Request) -> Response:
        time.sleep(1.0)  # client timeout is 0.5 s
        return Response(
            json.dumps({"id": "R-1", "status": "COMPLETED"}),
            status=201,
            content_type="application/json",
        )

    httpserver.expect_request(
        "/v2/payments/captures/CAP-1/refund", method="POST"
    ).respond_with_handler(slow)
    with pytest.raises(PayPalTimeout):
        money_writes.refund_capture(pp, "CAP-1", amount=None, request_id="k")


def test_PP_07_partial_refund_sends_amount_full_refund_sends_none(pp, httpserver):
    serve_token(httpserver)
    httpserver.expect_request(
        "/v2/payments/captures/CAP-1/refund", method="POST"
    ).respond_with_json({"id": "R-1", "status": "COMPLETED"}, status=201)
    money_writes.refund_capture(pp, "CAP-1", amount=money(1500), request_id="k1")
    money_writes.refund_capture(pp, "CAP-1", amount=None, request_id="k2")
    partial, full = (
        body_of(r) or {} for r in api_requests(httpserver, "/v2/payments/captures/CAP-1/refund")
    )
    assert partial["amount"] == {"value": "15.00", "currency_code": "GBP"}
    assert "amount" not in full


def test_PP_07_refund_5xx_retried_with_same_key_up_to_max_attempts(
    pp, httpserver, db, session_factory, clock, settings, rulebook
):
    seller = make_seller(db)
    order = make_order(db, seller, amount_minor=3000, paypal_order_id="O-1", capture_id="CAP-1")
    case = make_case(db, order)
    PolicyService(db, rulebook, clock).decide(case, engine_result(), facts_for(order))
    db.commit()
    serve_token(httpserver)
    httpserver.expect_request("/v2/checkout/orders/O-1", method="GET").respond_with_json(
        {**capture_body("COMPLETED"), "id": "O-1"}
    )
    httpserver.expect_request(
        "/v2/payments/captures/CAP-1/refund", method="POST"
    ).respond_with_json({"name": "INTERNAL_SERVER_ERROR"}, status=503)
    handler = RefundJobHandler(RestRefundGateway(pp), rulebook, clock)
    runner = JobRunner(session_factory, {REFUND_JOB: handler}, clock, settings)
    for _ in range(settings.max_attempts + 2):
        runner.run_due()
        clock.advance(10_000)
    sent = api_requests(httpserver, "/v2/payments/captures/CAP-1/refund")
    assert len(sent) == settings.max_attempts
    assert len({r.headers.get("PayPal-Request-Id") for r in sent}) == 1
    db.expire_all()
    assert db.get(type(case), case.id).state == CaseState.HOLD_FOR_REVIEW  # type: ignore[union-attr]


# ---------------------------------------------------------------- PP-08


def test_PP_08_get_capture_state_returns_status_and_refunded_total(pp, httpserver):
    serve_token(httpserver)
    order = capture_body("PARTIALLY_REFUNDED")
    order["purchase_units"][0]["payments"]["refunds"] = [
        {"id": "R1", "status": "COMPLETED", "amount": {"currency_code": "GBP", "value": "10.00"}},
        {"id": "R2", "status": "PENDING", "amount": {"currency_code": "GBP", "value": "5.00"}},
        {"id": "R3", "status": "FAILED", "amount": {"currency_code": "GBP", "value": "3.00"}},
        {"id": "R4", "status": "CANCELLED", "amount": {"currency_code": "GBP", "value": "2.00"}},
    ]
    httpserver.expect_request("/v2/checkout/orders/O-1", method="GET").respond_with_json(order)
    state = reads.get_capture_state(pp, "O-1", "CAP-1")
    assert state.capture_id == "CAP-1"
    assert state.status == "PARTIALLY_REFUNDED"
    assert state.amount == GBP60
    assert state.refunded_total == money(1500)


# ---------------------------------------------------------------- PP-09


def test_PP_09_provide_dispute_evidence_posts_multipart_evidence(pp, httpserver):
    serve_token(httpserver)
    httpserver.expect_request(
        "/v1/customer/disputes/D-1/provide-evidence", method="POST"
    ).respond_with_json({"links": []}, status=200)
    money_writes.provide_dispute_evidence(
        pp,
        "D-1",
        evidence=DisputeEvidence(
            evidence_type="PROOF_OF_FULFILLMENT",
            notes="Locker pickup",
            carrier="OTHER",
            tracking_number="SHIPPO_DELIVERED",
        ),
        request_id="dispute:D-1:provide_evidence",
    )
    req = api_requests(httpserver, "/v1/customer/disputes/D-1/provide-evidence")[-1]
    assert req.headers["Content-Type"].startswith("multipart/form-data")
    raw = req.get_data(as_text=True)
    assert "PROOF_OF_FULFILLMENT" in raw
    assert "SHIPPO_DELIVERED" in raw


@pytest.mark.xfail(
    reason="PP-09: limitation — dispute make-offer is not built (ADR-009)",
    raises=NotImplementedError,
    strict=True,
)
def test_PP_09_make_offer_not_built(pp):
    money_writes.make_dispute_offer(pp, "D-1", request_id="dispute:D-1:make_offer")


# ---------------------------------------------------------------- PP-10


def test_PP_10_update_shipping_address_sends_patch_on_shipping_address(pp, httpserver):
    serve_token(httpserver)
    httpserver.expect_request("/v2/checkout/orders/O-1", method="PATCH").respond_with_data(
        "", status=204
    )
    address = Address(
        name="Sam",
        line1="Flat 2, 10 High Street",
        city="London",
        postal_code="N1 9GU",
        country="GB",
    )
    order_writes.update_shipping_address(pp, "O-1", address, request_id="order:o1:patch_address")
    body = body_of(api_requests(httpserver, "/v2/checkout/orders/O-1")[-1])
    assert isinstance(body, list) and len(body) == 1
    op = body[0]
    assert op["op"] in ("replace", "add")
    assert op["path"].endswith("/shipping/address")
    assert op["value"]["address_line_1"] == "Flat 2, 10 High Street"
    assert op["value"]["admin_area_2"] == "London"
    assert op["value"]["postal_code"] == "N1 9GU"
    assert op["value"]["country_code"] == "GB"
