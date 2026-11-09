"""DP-01..05: a signed delivery proof that a buyer's AI assistant, or anyone, can check.

Agent-payment protocols sign what the buyer asked for and paid. None signs what happened at the
door. Doorstep signs the delivery facts it holds as a compact JWS (RFC 7515) with EdDSA
(RFC 8037), using its receipt key, and publishes the public key as a JWK set. A changed byte
fails the check, and a proof signed by any other key fails too.

The proof states facts, never a conclusion: the carrier's delivered scan, the possession seal
scanned after it, the scan-audit verdict, the case route and rule, and the head of the case's
receipt chain. It never says the buyer received the parcel, and it carries no address, name,
postcode, tracking number or buyer words (hard rules 7-9).
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client

from app.api.agent import OrderAgentPort
from app.mcp.server import MCP_SCOPE, build_mcp
from app.orders.enums import CaseState, OrderState
from app.orders.models import TrackingEvent
from app.proof.attestation import (
    TYP,
    InvalidProof,
    build_claims,
    jwks,
    sign_proof,
    verify_proof,
)
from app.proof.receipts import ReceiptSigner, issue_receipt
from app.proof.seal import record_scan
from app.security.links import LinkSigner
from tests.factories import add_decision, make_case, make_order, make_seller, make_shipment

ISS = "https://doorstep.example"


@pytest.fixture
def signer() -> ReceiptSigner:
    return ReceiptSigner.generate()


def _b64json(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def _delivered(db, clock, *, seal_after: bool = True, delivered: bool = True):
    order = make_order(db, make_seller(db), state=OrderState.IN_TRANSIT)
    shipment = make_shipment(db, order, tracking_number="SHIPPO_DELIVERED", carrier="evri")
    db.add(
        TrackingEvent(
            shipment_id=shipment.id,
            status="TRANSIT",
            occurred_at=clock.now() - timedelta(hours=30),
            applied=True,
        )
    )
    if delivered:
        db.add(
            TrackingEvent(
                shipment_id=shipment.id,
                status="DELIVERED",
                occurred_at=clock.now() - timedelta(hours=6),
                applied=True,
            )
        )
    db.flush()
    if seal_after:
        record_scan(db, order, clock.now() - timedelta(hours=2))
    return order


# ---------------------------------------------------------------- DP-01 facts only


def test_DP_01_the_proof_states_the_delivery_facts(db, clock):
    order = _delivered(db, clock)
    claims = build_claims(db, order, issuer=ISS, now=clock.now())
    assert claims.iss == ISS and claims.sub == order.id[:8].upper()
    assert claims.iat == int(clock.now().timestamp())
    assert claims.carrier == "evri"
    assert claims.delivered_scan_at == (clock.now() - timedelta(hours=6)).isoformat()
    assert claims.seal_scanned_after_delivery_at == (clock.now() - timedelta(hours=2)).isoformat()
    assert "delivered" in claims.statement and "seal" in claims.statement
    assert claims.case_state is None and claims.receipt_head is None


def test_DP_01_no_personal_data_and_no_conclusion(db, clock):
    order = _delivered(db, clock)
    text = claims_text = build_claims(db, order, issuer=ISS, now=clock.now()).model_dump_json()
    for private in ("High Street", "line1", "postal", "payer", "SHIPPO_DELIVERED", "PAYER-1"):
        assert private not in text
    for conclusion in ("received", "fraud", "lying", "stole"):
        assert conclusion not in claims_text.lower()


def test_DP_01_a_seal_scan_before_delivery_is_not_stated(db, clock):
    order = _delivered(db, clock, seal_after=False)
    record_scan(db, order, clock.now() - timedelta(hours=20))  # while in transit
    claims = build_claims(db, order, issuer=ISS, now=clock.now())
    assert claims.seal_scanned_after_delivery_at is None
    assert "seal" not in claims.statement  # not scanning after delivery proves nothing


def test_DP_01_no_delivered_scan_is_said_plainly(db, clock):
    order = _delivered(db, clock, seal_after=False, delivered=False)
    claims = build_claims(db, order, issuer=ISS, now=clock.now())
    assert claims.delivered_scan_at is None
    assert claims.statement.startswith("The carrier has not scanned the parcel as delivered")


def test_DP_01_a_case_adds_its_route_rule_audit_and_receipt_head(db, clock, signer):
    order = _delivered(db, clock)
    case = make_case(db, order, state=CaseState.AWAITING_SELLER)
    decision = add_decision(db, case)
    decision.rule_id, decision.rule_version = "uk_possession_v1", "2026-11-04"
    decision.signals = {"scan_audit": {"verdict": "contradicted"}}
    receipt = issue_receipt(
        db, signer, case_id=case.id, kind="decision", payload={}, at=clock.now()
    )
    db.flush()
    claims = build_claims(db, order, issuer=ISS, now=clock.now())
    assert claims.case_state == CaseState.AWAITING_SELLER.value
    assert claims.rule == "uk_possession_v1@2026-11-04"
    assert claims.scan_audit == "contradicted"
    assert claims.receipt_head == receipt.hash


# ---------------------------------------------------------------- DP-02 JWS, EdDSA


def test_DP_02_a_compact_jws_signed_with_eddsa(db, clock, signer):
    token = sign_proof(signer, build_claims(db, _delivered(db, clock), issuer=ISS, now=clock.now()))
    header, payload, _ = token.split(".")
    assert _b64json(header) == {"alg": "EdDSA", "typ": TYP, "kid": jwks(signer)["keys"][0]["kid"]}
    assert _b64json(payload)["iss"] == ISS
    assert verify_proof(signer.public_key_b64, token).iss == ISS


def test_DP_02_the_key_set_is_an_okp_ed25519_jwk_with_a_thumbprint_kid(signer):
    [key] = jwks(signer)["keys"]
    assert {k: key[k] for k in ("kty", "crv", "alg", "use")} == {
        "kty": "OKP",
        "crv": "Ed25519",
        "alg": "EdDSA",
        "use": "sig",
    }
    raw = base64.b64decode(signer.public_key_b64)
    assert base64.urlsafe_b64decode(key["x"] + "=") == raw
    assert len(key["kid"]) == 43  # RFC 7638 SHA-256 thumbprint, base64url without padding


@pytest.mark.parametrize("part", [0, 1, 2])
def test_DP_02_one_changed_character_fails(db, clock, signer, part):
    token = sign_proof(signer, build_claims(db, _delivered(db, clock), issuer=ISS, now=clock.now()))
    parts = token.split(".")
    middle = len(parts[part]) // 2
    flipped = "A" if parts[part][middle] != "A" else "B"
    parts[part] = parts[part][:middle] + flipped + parts[part][middle + 1 :]
    with pytest.raises(InvalidProof):
        verify_proof(signer.public_key_b64, ".".join(parts))


def test_DP_02_another_key_or_junk_fails(db, clock, signer):
    token = sign_proof(signer, build_claims(db, _delivered(db, clock), issuer=ISS, now=clock.now()))
    with pytest.raises(InvalidProof):
        verify_proof(ReceiptSigner.generate().public_key_b64, token)
    for junk in ("", "a.b", "a.b.c", "x" * 50, token + ".extra"):
        with pytest.raises(InvalidProof):
            verify_proof(signer.public_key_b64, junk)


def test_DP_02_a_proof_with_another_alg_fails(signer):
    head = base64.urlsafe_b64encode(b'{"alg":"none","typ":"x"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(b'{"iss":"x"}').rstrip(b"=").decode()
    with pytest.raises(InvalidProof):
        verify_proof(signer.public_key_b64, f"{head}.{body}.")


# ---------------------------------------------------------------- DP-03 public check


@pytest.fixture
def web(db, clock, session_factory, settings, signer):
    from app.api.deps import AppDeps, Services
    from app.main import create_app
    from app.runtime import ROOT

    links = LinkSigner("p" * 32, clock)  # a test-only signing secret
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=ROOT / "rules",
        services=Services(links=links, receipts=signer, orders=None, approved=None),
    )
    return TestClient(create_app(deps)), links


def test_DP_03_the_key_set_is_published(web, signer):
    api, _ = web
    r = api.get("/.well-known/jwks.json")
    assert r.status_code == 200 and r.json() == jwks(signer)


def test_DP_03_anyone_can_check_a_proof(db, clock, web, signer):
    api, _ = web
    token = sign_proof(signer, build_claims(db, _delivered(db, clock), issuer=ISS, now=clock.now()))
    ok = api.post("/api/proof/verify", json={"proof": token}).json()
    assert ok["valid"] is True and ok["claims"]["iss"] == ISS and ok["reason"] is None
    bad = api.post("/api/proof/verify", json={"proof": token[:-2] + "AA"}).json()
    assert bad["valid"] is False and bad["claims"] is None and bad["reason"]
    assert api.post("/api/proof/verify", json={"proof": "x" * 9000}).status_code == 422


# ---------------------------------------------------------------- DP-04 the buyer's proof


def test_DP_04_the_buyer_gets_a_proof_for_their_order(db, clock, web, signer):
    api, links = web
    order = _delivered(db, clock)
    db.commit()
    body = api.get(f"/api/buyer/{links.issue(order.id, scope='buyer')}/proof").json()
    claims = verify_proof(signer.public_key_b64, body["proof"])
    assert claims.sub == order.id[:8].upper() and body["claims"] == claims.model_dump()
    assert body["check_url"].endswith("/proof#" + body["proof"])
    assert api.get("/api/buyer/not-a-token/proof").status_code == 404


# ---------------------------------------------------------------- DP-05 for AI assistants


def test_DP_05_the_status_tool_answers_with_a_signed_proof(db, clock, session_factory, signer):
    from app.proof.attestation import Prover

    order = _delivered(db, clock)
    db.commit()
    links = LinkSigner("q" * 32, clock)
    prover = Prover(signer, issuer=ISS, clock=clock)
    server = build_mcp(links, OrderAgentPort(session_factory, lambda s: None, prover=prover))

    async def run():
        async with Client(server) as client:
            return await client.call_tool(
                "get_delivery_status", {"order_token": links.issue(order.id, scope=MCP_SCOPE)}
            )

    status = asyncio.run(run()).structured_content
    claims = verify_proof(signer.public_key_b64, status["delivery_proof"])
    assert claims.sub == status["order_ref"]
    assert status["proof_check_url"] == f"{ISS}/proof#{status['delivery_proof']}"
    assert status["proof_keys_url"] == f"{ISS}/.well-known/jwks.json"


def test_DP_05_without_a_prover_the_status_has_no_proof(db, clock, session_factory):
    order = _delivered(db, clock)
    db.commit()
    port = OrderAgentPort(session_factory, lambda s: None)
    status = port.status(order.id)
    assert status.delivery_proof is None and status.proof_check_url is None
