"""MC over HTTP: the buyer gets an agent link, and an AI agent calls the tool at /agent/mcp."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.agent import build_agent_http, mount_agent
from app.api.deps import AppDeps, Services
from app.main import create_app
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _app(session_factory, settings, clock):
    services = Services(
        links=LinkSigner("h" * 32, clock),
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
    app = create_app(deps)
    runtime = SimpleNamespace(
        deps=deps,
        services=services,
        clock=clock,
        settings=settings,
        session_factory=session_factory,
    )
    mount_agent(app, build_agent_http(runtime))
    return app


def _rpc(client: TestClient, method: str, params: dict, rid: int = 1) -> dict:
    r = client.post(
        "/agent/mcp",
        follow_redirects=False,
        headers=HEADERS,
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_MC_01_buyer_gets_an_agent_link_and_the_agent_reports(session_factory, settings, clock):
    app = _app(session_factory, settings, clock)
    with TestClient(app) as client:
        seed = client.post("/demo/seed?scratch=1").json()
        sam = seed["buyers"]["sam-jacket"]
        link = client.post(f"/api/buyer/{sam}/agent-link").json()
        assert link["mcp_url"].endswith("/agent/mcp")
        assert link["order_token"] != sam  # a separate, mcp-scoped token

        _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-agent", "version": "1"},
            },
        )
        tools = _rpc(client, "tools/list", {}, 2)["result"]["tools"]
        assert {t["name"] for t in tools} == {"report_delivery_problem", "get_delivery_status"}
        answer = _rpc(
            client,
            "tools/call",
            {
                "name": "report_delivery_problem",
                "arguments": {"order_token": link["order_token"], "reason": "not_received"},
            },
            3,
        )["result"]
        assert answer["isError"] is False
        assert answer["structuredContent"]["case_id"]

        # The buyer's own page now shows the case the agent opened.
        assert client.get(f"/api/buyer/{sam}").json()["case_open"] is True


def test_MC_02_a_bad_link_cannot_mint_an_agent_token(session_factory, settings, clock):
    with TestClient(_app(session_factory, settings, clock)) as client:
        assert client.post("/api/buyer/forged/agent-link").status_code == 404
