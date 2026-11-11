"""MC-05: the order token may arrive as `Authorization: Bearer <token>` or as the tool argument.
Scope and expiry checks are unchanged; two different tokens are refused."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.security.links import LinkSigner
from tests.unit.test_mc_http import HEADERS, _app

INIT = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "test-agent", "version": "1"},
}


def _call(client: TestClient, args: dict, bearer: str | None, tool: str = "get_delivery_status"):
    headers = dict(HEADERS)
    if bearer is not None:
        headers["Authorization"] = bearer
    r = client.post(
        "/agent/mcp",
        follow_redirects=False,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _tokens(client: TestClient) -> tuple[str, str]:
    seed = client.post("/demo/seed?scratch=1").json()
    buyer = seed["buyers"]["sam-jacket"]
    return buyer, client.post(f"/api/buyer/{buyer}/agent-link").json()["order_token"]


def test_MC_05_header_only_works(session_factory, settings, clock):
    with TestClient(_app(session_factory, settings, clock)) as client:
        _, token = _tokens(client)
        out = _call(client, {}, f"Bearer {token}")
        assert out["isError"] is False
        assert out["structuredContent"]["order_ref"]


def test_MC_05_argument_only_still_works(session_factory, settings, clock):
    with TestClient(_app(session_factory, settings, clock)) as client:
        _, token = _tokens(client)
        out = _call(client, {"order_token": token}, None)
        assert out["isError"] is False


def test_MC_05_the_same_token_in_both_places_works(session_factory, settings, clock):
    with TestClient(_app(session_factory, settings, clock)) as client:
        _, token = _tokens(client)
        assert _call(client, {"order_token": token}, f"Bearer {token}")["isError"] is False


def test_MC_05_two_different_tokens_are_refused(session_factory, settings, clock):
    with TestClient(_app(session_factory, settings, clock)) as client:
        _, token = _tokens(client)
        _, other = _tokens(client)
        out = _call(client, {"order_token": other}, f"Bearer {token}")
        assert out["isError"] is True
        assert "differ" in out["content"][0]["text"]


def test_MC_05_a_buyer_link_token_in_the_header_is_refused(session_factory, settings, clock):
    with TestClient(_app(session_factory, settings, clock)) as client:
        buyer, _ = _tokens(client)
        out = _call(
            client, {"reason": "not_received"}, f"Bearer {buyer}", "report_delivery_problem"
        )
        assert out["isError"] is True
        assert client.get(f"/api/buyer/{buyer}").json()["case_open"] is False


def test_MC_05_garbage_and_missing_tokens_are_refused(session_factory, settings, clock):
    with TestClient(_app(session_factory, settings, clock)) as client:
        assert _call(client, {}, "Bearer garbage")["isError"] is True
        assert _call(client, {}, "Basic abc")["isError"] is True  # not a bearer header: ignored
        assert _call(client, {}, None)["isError"] is True


def test_MC_05_an_expired_token_in_the_header_is_refused(session_factory, settings, clock):
    with TestClient(_app(session_factory, settings, clock)) as client:
        _, token = _tokens(client)
        assert _call(client, {}, f"Bearer {token}")["isError"] is False
        clock.advance(8 * 24 * 3600)  # past the 7-day agent link
        assert _call(client, {}, f"Bearer {token}")["isError"] is True


def test_MC_05_a_token_signed_with_another_key_is_refused(session_factory, settings, clock):
    with TestClient(_app(session_factory, settings, clock)) as client:
        forged = LinkSigner("x" * 32, clock).issue("whatever", scope="mcp")
        assert _call(client, {}, f"Bearer {forged}")["isError"] is True
