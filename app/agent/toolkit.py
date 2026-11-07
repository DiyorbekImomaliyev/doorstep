"""PayPal Agent Toolkit tools for the Doorstep agent, with an explicit allowlist (AT-01).

Only read actions are enabled. The toolkit's shipment *create* action is left off on purpose:
it would write to PayPal outside Doorstep's jobs outbox and idempotency keys (hard rule 4);
tracking is registered by the outbox job instead (TR-05). No refund, capture or
dispute-accept action is enabled; the only money tool is Doorstep's own request_refund.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

ToolFn = Callable[[dict[str, Any]], str]

ALLOWED_ACTIONS: dict[str, dict[str, bool]] = {
    "orders": {"get": True},
    "shipment": {"get": True},
    "disputes": {"list": True, "get": True},
}

PAYPAL_READ_TOOLS = frozenset(
    {"get_order_details", "get_shipment_tracking", "list_disputes", "get_dispute"}
)


def paypal_tools(client_id: str, secret: str) -> dict[str, ToolFn]:
    """The toolkit's LangChain tools for the allowlist, as name -> call(args) -> JSON text.

    Raises if the toolkit ever hands back a tool outside the allowlist."""
    from paypal_agent_toolkit.langchain.toolkit import PayPalToolkit
    from paypal_agent_toolkit.shared.configuration import Configuration, Context

    toolkit = PayPalToolkit(
        client_id=client_id,
        secret=secret,
        configuration=Configuration(actions=ALLOWED_ACTIONS, context=Context(sandbox=True)),
    )
    tools: dict[str, ToolFn] = {}
    for tool in toolkit.get_tools():
        if tool.name not in PAYPAL_READ_TOOLS:
            raise RuntimeError(f"toolkit returned a tool outside the allowlist: {tool.name}")

        def call(args: dict[str, Any], _tool: Any = tool) -> str:
            return str(_tool.invoke(args))

        tools[tool.name] = call
    return tools
