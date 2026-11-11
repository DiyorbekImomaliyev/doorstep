"""MC-04: a buyer's AI assistant using Doorstep end to end over MCP, against any base URL.

    uv run python scripts/assistant_demo.py [--base http://127.0.0.1:8000]
                                            [--order-token T --mcp-url U]

It deliberately imports nothing from `app`: it speaks only HTTP, MCP and the public JWKS, as an
outside assistant would, and checks the signed delivery proof with the `cryptography` library.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

FetchJson = Callable[[str], Awaitable[dict[str, Any]]]
Say = Callable[[str], None]


class ProofInvalid(Exception):
    pass


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify_jws(token: str, jwks: dict[str, Any]) -> dict[str, Any]:
    """Check a compact JWS (EdDSA / Ed25519) against a JWK set; return its claims."""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_unb64(head_b64))
        if header.get("alg") != "EdDSA":
            raise ProofInvalid("unexpected algorithm")
        jwk = next(k for k in jwks["keys"] if k.get("kid") == header.get("kid"))
        key = Ed25519PublicKey.from_public_bytes(_unb64(jwk["x"]))
        key.verify(_unb64(sig_b64), f"{head_b64}.{body_b64}".encode("ascii"))
        return json.loads(_unb64(body_b64))
    except (ValueError, KeyError, StopIteration, InvalidSignature) as exc:
        raise ProofInvalid(str(exc) or type(exc).__name__) from None


def tamper(token: str) -> str:
    """The same proof with one character of the signed facts changed."""
    head, body, sig = token.split(".")
    flipped = "B" if body[10] != "B" else "C"
    return ".".join([head, body[:10] + flipped + body[11:], sig])


async def run_session(
    mcp: Client, fetch_json: FetchJson, order_token: str, say: Say, *, bearer: bool = True
) -> None:
    """Steps b to e of the transcript. `mcp` is an open client; with `bearer` the token travels
    in the Authorization header the client was built with, so tool calls carry no token."""
    args: dict[str, Any] = {} if bearer else {"order_token": order_token}

    tools = sorted(t.name for t in await mcp.list_tools())
    say(f"2  connected over MCP; tools: {', '.join(tools)}")

    status = (await mcp.call_tool("get_delivery_status", args)).structured_content or {}
    say(f"3  status: {status['item']}, {status['status']} (order {status['order_ref']})")

    proof, keys_url = status.get("delivery_proof"), status.get("proof_keys_url")
    if not proof or not keys_url:
        raise ProofInvalid("the status carries no signed proof")
    jwks = await fetch_json(keys_url)
    claims = verify_jws(proof, jwks)
    say(f"4  proof verified with Ed25519 key {jwks['keys'][0]['kid'][:8]}: {claims['statement']}")
    try:
        verify_jws(tamper(proof), jwks)
    except ProofInvalid:
        say("5  one character changed: proof REJECTED, as it should be")
    else:
        raise ProofInvalid("a tampered proof was accepted")

    answer = (
        await mcp.call_tool("report_delivery_problem", {**args, "reason": "not_received"})
    ).structured_content or {}
    say(f"6  reported not received: case {answer['case_id'][:8]}, route {answer['status']}")
    say(f"7  Doorstep says: {answer['message']}")
    say("8  the case now waits for the seller; no refund tool exists for an assistant")


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="A buyer's AI assistant using Doorstep over MCP")
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--order-token")
    p.add_argument("--mcp-url")
    a = p.parse_args(argv)
    base = a.base.rstrip("/")

    def say(line: str) -> None:
        print(line, flush=True)

    async with httpx.AsyncClient(timeout=30) as http:
        token, url = a.order_token, a.mcp_url
        if not token or not url:
            seed = (await http.post(f"{base}/demo/seed", params={"scratch": 1})).json()
            buyer = seed["buyers"]["sam-jacket"]
            link = (await http.post(f"{base}/api/buyer/{buyer}/agent-link")).json()
            token, url = link["order_token"], link["mcp_url"]
            say(f"1  scratch shop seeded; buyer link gave an order token ({len(token)} chars)")
        else:
            say("1  using the order token you gave")

        async def fetch_json(u: str) -> dict[str, Any]:
            return (await http.get(u)).json()

        transport = StreamableHttpTransport(url, headers={"Authorization": f"Bearer {token}"})
        try:
            async with Client(transport) as mcp:
                await run_session(mcp, fetch_json, token, say)
        except ProofInvalid as exc:
            say(f"FAILED: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
