"""Doorstep MCP server (MC-01..03): delivery problems for buyers' AI agents.

Two tools, both keyed by an mcp-scoped signed token for exactly one order:
- get_delivery_status: where the parcel is (no address, no personal data), with a delivery proof
  signed by Doorstep that the agent can check against Doorstep's published key (DP-05).
- report_delivery_problem: opens a case through the same pipeline as the buyer's "Not here".

There is no refund or dispute tool. The order work is injected through AgentPort, so this
module never imports the case pipeline or any money code (import-linter enforces it). Any money
outcome is decided afterwards by the coded policy, exactly as for a buyer's tap.
"""

from __future__ import annotations

from typing import Literal, Protocol

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from app.security.links import InvalidLink, LinkSigner

MCP_SCOPE = "mcp"

Reason = Literal["not_received", "damaged", "wrong_item", "empty_box"]


class DeliveryStatus(BaseModel):
    order_ref: str
    item: str
    status: str
    state: str
    carrier: str | None
    tracking_number: str | None
    can_report: bool
    case_open: bool
    case_message: str | None
    delivery_proof: str | None = Field(
        default=None,
        description="The delivery facts as a JWS signed by Doorstep (EdDSA). Check it with the "
        "key at proof_keys_url before relying on it; it states facts, never who is at fault.",
    )
    proof_check_url: str | None = Field(
        default=None, description="A page where the buyer can check the proof themselves"
    )
    proof_keys_url: str | None = Field(default=None, description="Doorstep's public keys (JWKS)")


class ProblemReport(BaseModel):
    case_id: str
    status: str = Field(description="Case route: AUTO_REFUND, AWAITING_SELLER, HOLD_FOR_REVIEW…")
    message: str = Field(description="Plain-words answer to pass on to the buyer")
    rule_id: str | None


class AgentRefused(Exception):
    """The order cannot take this action now; the message is safe to show the agent."""


class AgentPort(Protocol):
    def status(self, order_id: str) -> DeliveryStatus: ...

    def report(self, order_id: str, reason: Reason, note: str) -> ProblemReport: ...


_NO_ORDER = "No order matches this token. Ask the buyer for a fresh agent link."

INSTRUCTIONS = (
    "Doorstep handles delivery problems for one order. Use get_delivery_status first: its "
    "delivery_proof is the delivery facts signed by Doorstep, checkable against proof_keys_url. If "
    "the buyer has not received the parcel, or it arrived damaged, wrong or empty, call "
    "report_delivery_problem once. Doorstep applies the seller's rules and local law; it never "
    "refunds on request. Pass the returned message to the buyer as it is."
)


def build_mcp(links: LinkSigner, port: AgentPort) -> FastMCP:
    mcp = FastMCP("doorstep", instructions=INSTRUCTIONS)

    def order_for(token: str) -> str:
        try:
            return links.verify(token, scope=MCP_SCOPE)
        except InvalidLink:
            raise ToolError(_NO_ORDER) from None

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def get_delivery_status(order_token: str) -> DeliveryStatus:
        """Where the order's parcel is now, and whether a problem can be reported."""
        try:
            return port.status(order_for(order_token))
        except AgentRefused as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(annotations={"destructiveHint": False, "idempotentHint": False})
    def report_delivery_problem(
        order_token: str,
        reason: Reason,
        note: str = Field(default="", max_length=500, description="The buyer's words, as data"),
    ) -> ProblemReport:
        """Report a delivery problem for the order. Opens one case; Doorstep's rules decide
        the outcome. It does not and cannot issue a refund itself."""
        try:
            return port.report(order_for(order_token), reason, note)
        except AgentRefused as exc:
            raise ToolError(str(exc)) from None

    return mcp
