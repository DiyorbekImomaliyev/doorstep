"""SE-04: Schemathesis generates requests from the OpenAPI schema and checks every answer:
no 500s, and every documented response matches its schema. Run with `make fuzz`."""

from __future__ import annotations

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.checks import (
    content_type_conformance,
    not_a_server_error,
    response_schema_conformance,
)

from app.api.deps import AppDeps, Services
from app.main import create_app
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT, NotConfigured
from app.security.links import LinkSigner


@pytest.fixture
def api_schema(session_factory, settings, clock):
    orders = NotConfigured("PayPal", "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET")
    services = Services(
        links=LinkSigner("f" * 32, clock),
        receipts=ReceiptSigner.generate(),
        orders=orders,
        approved=orders,
    )
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=ROOT / "rules",
        services=services,
    )
    return schemathesis.openapi.from_asgi("/openapi.json", create_app(deps))


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
def test_SE_04_no_server_errors_and_responses_match_the_schema(case):
    case.call_and_validate(
        checks=[not_a_server_error, response_schema_conformance, content_type_conformance]
    )
