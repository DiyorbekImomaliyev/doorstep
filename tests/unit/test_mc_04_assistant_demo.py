"""MC-04: the buyer-assistant demo script, run against the in-process MCP server. The script
imports no app code, so these tests also show an outsider can check the signed proof."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest
from fastmcp import Client

from app.ai.explainer import Explainer
from app.api.agent import OrderAgentPort
from app.cases.pipeline import CasePipeline
from app.mcp.server import MCP_SCOPE, build_mcp
from app.orders.enums import OrderState
from app.proof.attestation import Prover, jwks
from app.proof.receipts import ReceiptSigner
from app.security.links import LinkSigner
from tests.factories import make_order, make_seller

_SPEC = importlib.util.spec_from_file_location(
    "assistant_demo", Path(__file__).resolve().parents[2] / "scripts" / "assistant_demo.py"
)
assert _SPEC and _SPEC.loader
demo = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(demo)


def _server(session_factory, rulebook, clock, settings, *, with_prover: bool):
    signer = ReceiptSigner.generate()
    links = LinkSigner("m" * 32, clock)

    def pipeline(s):
        return CasePipeline(s, rulebook, clock, settings, Explainer(), signer)

    prover = Prover(signer, issuer="https://doorstep.test", clock=clock) if with_prover else None
    server = build_mcp(links, OrderAgentPort(session_factory, pipeline, prover=prover))
    with session_factory() as s:
        order = make_order(s, make_seller(s), state=OrderState.DELIVERED)
        s.commit()
        token = links.issue(order.id, scope=MCP_SCOPE)
    return server, token, jwks(signer)


@pytest.fixture
def world(session_factory, rulebook, clock, settings):
    return _server(session_factory, rulebook, clock, settings, with_prover=True)


def test_MC_04_the_session_runs_end_to_end_and_rejects_a_tampered_proof(world):
    server, token, keys = world
    lines: list[str] = []
    fetched: list[str] = []

    async def fetch(url: str):
        fetched.append(url)
        return keys

    async def go():
        async with Client(server) as mcp:
            await demo.run_session(mcp, fetch, token, lines.append, bearer=False)

    asyncio.run(go())
    assert fetched == ["https://doorstep.test/.well-known/jwks.json"]
    text = "\n".join(lines)
    assert "proof verified" in text and "REJECTED" in text
    assert "reported not received" in text and "waits for the seller" in text
    assert len(lines) == 7


def test_MC_04_verify_jws_checks_signature_and_key(world):
    server, token, keys = world

    async def proof() -> str:
        async with Client(server) as mcp:
            r = await mcp.call_tool("get_delivery_status", {"order_token": token})
            return (r.structured_content or {})["delivery_proof"]

    jws = asyncio.run(proof())
    assert demo.verify_jws(jws, keys)["sub"]
    with pytest.raises(demo.ProofInvalid):
        demo.verify_jws(demo.tamper(jws), keys)
    with pytest.raises(demo.ProofInvalid):
        demo.verify_jws(jws, jwks(ReceiptSigner.generate()))  # not this key set
    with pytest.raises(demo.ProofInvalid):
        demo.verify_jws("not.a.proof", keys)


def test_MC_04_a_status_without_proof_fails(session_factory, rulebook, clock, settings):
    server, token, _ = _server(session_factory, rulebook, clock, settings, with_prover=False)

    async def go():
        async with Client(server) as mcp:
            await demo.run_session(mcp, None, token, lambda _l: None, bearer=False)  # type: ignore[arg-type]

    with pytest.raises(demo.ProofInvalid):
        asyncio.run(go())
