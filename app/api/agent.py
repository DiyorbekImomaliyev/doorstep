"""Adapter between the MCP server and the order system (MC-01..03), and its HTTP mount.

The MCP module sees only AgentPort; this adapter runs the real buyer view and case pipeline,
recording the reporter as "agent" so the seller sees who reported.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker
from starlette.routing import Mount

from app.api.views import buyer_view
from app.cases.engine import ClaimReason
from app.cases.pipeline import BuyerActionError, CasePipeline
from app.mcp.server import AgentRefused, DeliveryStatus, ProblemReport, Reason
from app.orders.models import Order
from app.proof.attestation import Prover

AGENT_REPORTER = "agent"
MCP_MOUNT = "/agent"
MCP_PATH = "/mcp"


class OrderAgentPort:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        pipeline: Callable[[Session], CasePipeline],
        *,
        prover: Prover | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.pipeline = pipeline
        self.prover = prover  # DP-05: signs the delivery facts on every status answer

    def _order(self, s: Session, order_id: str) -> Order:
        order = s.get(Order, order_id)
        if order is None:
            raise AgentRefused("No order matches this token. Ask the buyer for a fresh agent link.")
        return order

    def status(self, order_id: str) -> DeliveryStatus:
        with self.session_factory() as s:
            order = self._order(s, order_id)
            v = buyer_view(s, order)
            proof = self.prover.prove(s, order)[0] if self.prover else None
            return DeliveryStatus(
                order_ref=v.order_ref,
                item=v.item,
                status=v.status,
                state=v.state,
                carrier=v.carrier,
                tracking_number=v.tracking_number,
                can_report=v.can_report,
                case_open=v.case_open,
                case_message=v.case_message,
                delivery_proof=proof,
                proof_check_url=self.prover.check_url(proof) if self.prover and proof else None,
                proof_keys_url=self.prover.keys_url if self.prover else None,
            )

    def report(self, order_id: str, reason: Reason, note: str) -> ProblemReport:
        with self.session_factory() as s:
            order = self._order(s, order_id)
            try:
                report = self.pipeline(s).report(
                    order, ClaimReason(reason), reported_by=AGENT_REPORTER, note=note[:500]
                )
            except BuyerActionError:
                raise AgentRefused(
                    "This order can't be reported right now: it may not have shipped yet, "
                    "or a case is already open. Check get_delivery_status."
                ) from None
            s.commit()
            return ProblemReport(
                case_id=report.case_id,
                status=report.route.value,
                message=report.buyer_message,
                rule_id=report.rule_id,
            )


def mount_agent(app: FastAPI, mcp_http: Any) -> None:
    """Serve the MCP server at /agent/mcp, ahead of the web app's catch-all, and run its
    lifespan inside the API's (its session manager must be running to answer)."""
    app.router.routes.insert(0, Mount(MCP_MOUNT, app=mcp_http))
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: Any) -> AsyncIterator[None]:
        async with mcp_http.lifespan(a), previous(a):
            yield

    app.router.lifespan_context = lifespan


def build_agent_http(runtime: Any) -> Any:
    """The MCP server over streamable HTTP (stateless, JSON answers), wired to the runtime."""
    from app.ai.explainer import Explainer
    from app.mcp.server import build_mcp
    from app.policy.rules import load_rules

    rulebook = load_rules(runtime.deps.rules_dir)
    services = runtime.services
    explainer = services.explainer or Explainer()  # None means template text only

    def pipeline(s: Session) -> CasePipeline:
        return CasePipeline(
            s,
            rulebook,
            runtime.clock,
            runtime.settings,
            explainer,
            services.receipts,
        )

    prover = Prover(services.receipts, issuer=services.app_base_url, clock=runtime.clock)
    port = OrderAgentPort(runtime.session_factory, pipeline, prover=prover)
    server = build_mcp(services.links, port)
    return server.http_app(path=MCP_PATH, stateless_http=True, json_response=True)
