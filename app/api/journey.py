"""Buyer, seller, checkout, proof and demo routes (SPEC section 5 screens).

Auth model (SPEC section 13): buyers act through a signed order-scoped link (scope "buyer"); the
seller through a signed seller link (scope "seller", subject = seller id). A bad, expired or
wrong-scope token, and any id outside the token's scope, answer 404 so nothing leaks (BC-01,
BC-04). `GET /cases/{id}/verify` is public by design: receipts hold ids and amounts only.
"""

from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.runner import AgentRun, DoorstepAgent, simulated_tools
from app.ai.explainer import Explainer
from app.ai.insights_agent import InsightAnswer, InsightsAgent
from app.ai.llm import Image
from app.ai.packing import PackingChecker
from app.ai.policy_reader import PolicyReader, ReaderAnswer
from app.ai.recovery import RecoveryDrafter
from app.api.agent import MCP_MOUNT, MCP_PATH
from app.api.deps import SIMULATED_COMPONENTS, AppDeps, Services
from app.api.insights import InsightsOut, insights
from app.api.views import (
    BuyerView,
    CaseView,
    OrderRow,
    RecoveryView,
    buyer_view,
    case_recovery,
    case_view,
    order_row,
    recovery_view,
)
from app.cases.actions import CaseActions, seller_approver
from app.cases.counterfactual import PreventionSuggestion, prevention
from app.cases.dispute_evidence import UnsafeEvidence
from app.cases.engine import ClaimReason
from app.cases.pipeline import BuyerActionError, CasePipeline
from app.cases.recovery import ClaimDraftOnOpen, RecoveryError, SellerMark, seller_mark
from app.checkout.address import AddressChecker, AddressCheckResult
from app.checkout.service import CheckoutError, CheckoutService, DeliveryQuote
from app.db import Base
from app.demo.seed import DEMO_TAG, SeedSummary, seed_scenario
from app.mcp.server import MCP_SCOPE
from app.money import Currency, Money
from app.orders.enums import OrderState, ShippingMethod
from app.orders.models import AuditLog, Case, Order, Recovery, Seller
from app.orders.schemas import Address
from app.orders.state import IllegalTransition
from app.policy.rules import RuleBook
from app.policy.service import PolicyError
from app.proof.receipts import VerifyReport, verify_chain
from app.proof.seal import (
    SEAL_SCOPE,
    SELLER_BROWSER,
    SELLER_BROWSER_SCOPE,
    SELLER_COOKIE,
    SELLER_COOKIE_TTL_S,
    SealError,
    counted_scan,
    issue_seal,
    record_scan,
    seal_scans,
    seal_svg,
    seal_url,
    seller_browser_for,
)
from app.security.links import InvalidLink
from app.shipping.packing import PackingError, PackingService
from app.shipping.service import ShippingError, ShippingService
from app.worker.models import WebhookInbox
from app.worker.simulated import is_simulated

log = logging.getLogger("doorstep.api")

BUYER, SELLER = "buyer", "seller"
# AT-06: opening a case again within this many seconds, in the same state, reuses the agent run.
AGENT_REUSE_S = 600


def _session(request: Request) -> Iterator[Session]:
    with request.app.state.deps.session_factory() as s:
        yield s


Db = Annotated[Session, Depends(_session)]


class AmountIn(BaseModel):
    amount_minor: int = Field(gt=0, le=10_000_000)
    currency: Currency


class QuoteIn(AmountIn):
    seller_id: str
    buyer_country: str = Field(min_length=2, max_length=2)


class StartIn(QuoteIn):
    method: ShippingMethod
    item_description: str = Field(default="", max_length=200)


class StartOut(BaseModel):
    order_id: str
    paypal_order_id: str
    approve_url: str
    recommended: ShippingMethod
    buyer_token: str
    buyer_url: str


class ReturnIn(BaseModel):
    paypal_order_id: str = Field(min_length=1, max_length=64)


class ReturnOut(BaseModel):
    buyer_token: str
    buyer_url: str
    address: AddressCheckResult | None = None


class ReportIn(BaseModel):
    reason: ClaimReason = ClaimReason.NOT_RECEIVED
    note: str = Field(default="", max_length=1000)  # data, never instructions (hard rule 8)


class ReportOut(BaseModel):
    case_id: str
    status: str
    message: str
    rights: str = ""  # RT-01: the matching rule file's buyer_rights text


class SealCardOut(BaseModel):
    """SL-01: what the seller prints (QR on the packing slip, or the URL on an NFC sticker)."""

    order_ref: str
    url: str
    svg: str


class SealView(BaseModel):
    """SL-02: item and seller only; never the address."""

    order_ref: str
    item: str
    seller_name: str
    status: Literal["not_shipped", "ready", "confirmed", "case_open"]
    scanned: bool
    counted: bool
    seller_browser: bool = False  # SL-07: this response answered a scan from the seller's browser


class RecoveryMarkIn(BaseModel):
    amount_minor: int | None = Field(default=None, ge=1)  # "paid" only


class RecoverySummary(BaseModel):
    """RC-05: money recovered from carriers and claims still open, per currency."""

    recovered_minor: dict[str, int]
    open_claims: int
    open_claim_minor: dict[str, int]


class SealReportIn(BaseModel):
    reason: Literal[ClaimReason.DAMAGED, ClaimReason.WRONG_ITEM, ClaimReason.MISSING_ITEM] = (
        ClaimReason.DAMAGED
    )


class ChoiceIn(BaseModel):
    option: str = Field(max_length=16)


class ShipIn(BaseModel):
    carrier: str = Field(default="shippo", max_length=64)
    tracking_number: str = Field(min_length=1, max_length=128)
    label_weight_g: int | None = Field(default=None, gt=0, le=100_000)


_PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_PHOTO_BYTES = 5 * 1024 * 1024


class PolicyQuestionIn(BaseModel):
    question: str | None = Field(default=None, max_length=500)


class InsightQuestionIn(BaseModel):
    question: str = Field(min_length=2, max_length=300)


_REASON_QUESTION = {
    ClaimReason.NOT_RECEIVED.value: "What happens if a parcel did not arrive or was not received?",
    ClaimReason.DAMAGED.value: "What happens if the item arrived damaged?",
    ClaimReason.WRONG_ITEM.value: "What happens if the wrong item was delivered?",
    ClaimReason.EMPTY_BOX.value: "What happens if the parcel arrived empty?",
    ClaimReason.MISSING_ITEM.value: "What happens if an item is missing from the parcel?",
}


class PackingIn(BaseModel):
    """One packing photo, base64 encoded (a phone camera JPEG is well under the cap)."""

    image_b64: str = Field(min_length=8, max_length=MAX_PHOTO_BYTES * 4 // 3 + 8)
    media_type: str = Field(default="image/jpeg", max_length=32)


class PackingOut(BaseModel):
    verdict: str
    attribute: str | None
    message: str
    reasons: list[str]
    order: OrderRow


AGENT_LINK_TTL_S = 7 * 24 * 3600


class AgentLinkOut(BaseModel):
    order_token: str
    mcp_url: str
    tool: str
    expires_in_days: int


class ActionOut(BaseModel):
    case_id: str
    state: str
    money_action_id: str | None = None


class SeedOut(BaseModel):
    tag: str
    seller_token: str
    seller_url: str
    buyers: dict[str, str]
    summary: SeedSummary


class ResetIn(BaseModel):
    confirm: str


class WebhookStat(BaseModel):
    """Counts and times only: no payloads, ids or personal data."""

    received: int
    last_received_at: datetime | None
    last_event_type: str | None
    last_status: str | None


class Health(BaseModel):
    ok: bool
    environment: str
    rules: list[str]
    simulated: dict[str, str]
    receipts_public_key: str
    model: str
    webhooks: dict[str, WebhookStat] = {}


def build_journey_router(deps: AppDeps, services: Services, rulebook: RuleBook) -> APIRouter:
    router = APIRouter()
    explainer: Explainer = services.explainer or Explainer()
    # AS-06: one per app, so its model budget and answer cache are shared by every seller.
    ask_doorstep = InsightsAgent(services.agent_llm)

    def _subject(token: str, scope: str) -> str:
        try:
            return services.links.verify(token, scope=scope)
        except InvalidLink:
            raise HTTPException(404, "not found") from None

    def _order(s: Session, token: str) -> Order:
        order = s.get(Order, _subject(token, BUYER))
        if order is None:
            raise HTTPException(404, "not found")
        return order

    def _seller(s: Session, token: str) -> Seller:
        seller = s.get(Seller, _subject(token, SELLER))
        if seller is None:
            raise HTTPException(404, "not found")
        return seller

    def _pipeline(s: Session) -> CasePipeline:
        return CasePipeline(s, rulebook, deps.clock, deps.settings, explainer, services.receipts)

    def _checkout(s: Session) -> CheckoutService:
        return CheckoutService(
            s,
            services.orders,
            services.approved,
            AddressChecker(services.address_validator),
            rulebook,
            deps.clock,
            deps.settings,
        )

    def _seal_order(s: Session, token: str) -> Order:
        order = s.get(Order, _subject(token, SEAL_SCOPE))
        if order is None:
            raise HTTPException(404, "not found")
        return order

    def _rights(order: Order) -> str:
        rule = rulebook.select(order.buyer_country)
        return rule.buyer_rights if rule is not None else ""

    def _mark_seller_browser(response: Response, seller: Seller) -> None:
        """SL-07: a signed, HttpOnly cookie so this browser's seal scans never count."""
        response.set_cookie(
            SELLER_COOKIE,
            services.links.issue(seller.id, scope=SELLER_BROWSER_SCOPE, ttl_s=SELLER_COOKIE_TTL_S),
            max_age=SELLER_COOKIE_TTL_S,
            path="/",
            httponly=True,
            samesite="lax",
            secure=services.app_base_url.startswith("https://"),
        )

    def _from_seller_browser(request: Request, order: Order) -> bool:
        return seller_browser_for(
            services.links, request.cookies.get(SELLER_COOKIE), order.seller_id
        )

    def _seal_view(s: Session, order: Order, *, seller_browser: bool = False) -> SealView:
        seller = s.get(Seller, order.seller_id)
        state = OrderState(order.state)
        status: Literal["not_shipped", "ready", "confirmed", "case_open"]
        if state in (OrderState.CREATED, OrderState.PAID, OrderState.PACKED):
            status = "not_shipped"
        elif state in (OrderState.CASE_OPEN, OrderState.RESOLVED):
            status = "case_open"
        elif state == OrderState.CONFIRMED:
            status = "confirmed"
        else:
            status = "ready"
        return SealView(
            order_ref=order.id[:8].upper(),
            item=order.item_description or "Your order",
            seller_name=seller.name if seller else "",
            status=status,
            scanned=bool(seal_scans(s, order.id)),
            counted=counted_scan(s, order.id) is not None,
            seller_browser=seller_browser,
        )

    def _buyer_url(token: str) -> str:
        return f"{services.app_base_url}/t/{token}"

    def _seller_url(token: str) -> str:
        return f"{services.app_base_url}/seller/{token}"

    # ------------------------------------------------------------ public

    @router.get("/healthz", response_model=Health)
    def healthz(s: Db) -> Health:
        webhooks = {}
        for source in ("paypal", "shippo"):
            count = s.scalar(
                select(func.count(WebhookInbox.id)).where(WebhookInbox.source == source)
            )
            last = s.scalars(
                select(WebhookInbox)
                .where(WebhookInbox.source == source)
                .order_by(WebhookInbox.received_at.desc())
                .limit(1)
            ).first()
            webhooks[source] = WebhookStat(
                received=count or 0,
                last_received_at=last.received_at if last else None,
                last_event_type=last.event_type if last else None,
                last_status=last.status if last else None,
            )
        return Health(
            webhooks=webhooks,
            ok=True,
            environment=services.environment,
            rules=sorted(rulebook.rule_ids),
            simulated=SIMULATED_COMPONENTS,
            receipts_public_key=services.receipts.public_key_b64,
            model="configured" if services.explainer and services.explainer.llm else "template",
        )

    @router.get("/cases/{case_id}/verify", response_model=VerifyReport)
    def verify(case_id: str, s: Db) -> VerifyReport:
        """PR-02: public; recomputes the chain up to this case's last receipt."""
        return verify_chain(s, services.receipts.public_key_b64, case_id=case_id)

    # ------------------------------------------------------------ checkout

    @router.post("/api/checkout/quote", response_model=DeliveryQuote)
    def quote(body: QuoteIn, s: Db) -> DeliveryQuote:
        if s.get(Seller, body.seller_id) is None:
            raise HTTPException(404, "unknown seller")
        amount = Money(amount_minor=body.amount_minor, currency=body.currency)
        return _checkout(s).quote(body.seller_id, amount, buyer_country=body.buyer_country)

    @router.post("/api/checkout/start", response_model=StartOut)
    def start(body: StartIn, s: Db) -> StartOut:
        amount = Money(amount_minor=body.amount_minor, currency=body.currency)
        try:
            started = _checkout(s).start(
                body.seller_id,
                amount,
                body.method,
                buyer_country=body.buyer_country,
                item_description=body.item_description,
            )
        except CheckoutError as exc:
            raise HTTPException(404, str(exc)) from None
        s.commit()
        token = services.links.issue(started.order_id, scope=BUYER)
        return StartOut(
            order_id=started.order_id,
            paypal_order_id=started.paypal_order_id,
            approve_url=started.approve_url,
            recommended=started.recommended,
            buyer_token=token,
            buyer_url=_buyer_url(token),
        )

    @router.post("/api/checkout/return", response_model=ReturnOut)
    def paypal_return(body: ReturnIn, s: Db) -> ReturnOut:
        """PayPal sends the buyer back with ?token=<PayPal order id>. That id is unguessable and
        known only to the buyer's browser; it is exchanged once for the signed buyer link."""
        order = s.scalars(
            select(Order).where(Order.paypal_order_id == body.paypal_order_id)
        ).first()
        if order is None:
            raise HTTPException(404, "not found")
        result = None
        if order.state == "CREATED" and order.address_check in ("unknown", "invalid"):
            try:
                result = _checkout(s).on_approved(order)
            except CheckoutError as exc:
                raise HTTPException(409, str(exc)) from None
            s.commit()
        token = services.links.issue(order.id, scope=BUYER)
        return ReturnOut(buyer_token=token, buyer_url=_buyer_url(token), address=result)

    @router.post("/api/buyer/{token}/approved", response_model=AddressCheckResult)
    def approved(token: str, s: Db) -> AddressCheckResult:
        order = _order(s, token)
        try:
            result = _checkout(s).on_approved(order)
        except CheckoutError as exc:
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return result

    @router.post("/api/buyer/{token}/address", response_model=AddressCheckResult)
    def fix_address(token: str, body: Address, s: Db) -> AddressCheckResult:
        order = _order(s, token)
        try:
            result = _checkout(s).confirm_address(order, body)
        except CheckoutError as exc:
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return result

    @router.post("/api/buyer/{token}/keep-address", response_model=BuyerView)
    def keep_address(token: str, s: Db) -> BuyerView:
        order = _order(s, token)
        try:
            _checkout(s).keep_address(order)
        except CheckoutError as exc:
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return buyer_view(s, order)

    # ------------------------------------------------------------ buyer tracker

    @router.get("/api/buyer/{token}", response_model=BuyerView)
    def buyer(token: str, s: Db) -> BuyerView:
        order = _order(s, token)
        view = buyer_view(s, order)
        if view.case_open:
            view.rights = _rights(order)
        return view

    @router.post("/api/buyer/{token}/got-it", response_model=BuyerView)
    def got_it(token: str, request: Request, s: Db) -> BuyerView:
        order = _order(s, token)
        actor = SELLER_BROWSER if _from_seller_browser(request, order) else BUYER  # SL-08
        try:
            _pipeline(s).confirm_received(order, actor=actor)
        except BuyerActionError as exc:
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return buyer_view(s, order)

    @router.post("/api/buyer/{token}/not-here", response_model=ReportOut)
    def not_here(token: str, body: ReportIn, s: Db) -> ReportOut:
        order = _order(s, token)
        try:
            report = _pipeline(s).report(order, body.reason, reported_by=BUYER, note=body.note)
        except BuyerActionError as exc:
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return ReportOut(
            case_id=report.case_id,
            status=report.route.value,
            message=report.buyer_message,
            rights=_rights(order),
        )

    # ------------------------------------------------------------ possession seal (SL)

    @router.get("/api/seal/{token}", response_model=SealView)
    def seal_page(token: str, request: Request, s: Db) -> SealView:
        order = _seal_order(s, token)
        return _seal_view(s, order, seller_browser=_from_seller_browser(request, order))

    def _preview_scan(s: Session, order: Order, action: str) -> None:
        """SL-07: the seller's own browser gets a preview. The scan is recorded, never counted,
        and nothing else changes: no confirmation, no case."""
        try:
            record_scan(s, order, deps.clock.now(), seller_browser=True)
        except SealError as exc:
            raise HTTPException(409, str(exc)) from None
        s.add(
            AuditLog(
                actor=SELLER_BROWSER,
                action=action,
                entity_type="order",
                entity_id=order.id,
                before={},
                after={"counted": False, "reason": SELLER_BROWSER},
                at=deps.clock.now(),
            )
        )
        s.commit()

    @router.post("/api/seal/{token}/confirm", response_model=SealView)
    def seal_confirm(token: str, request: Request, s: Db) -> SealView:
        """SL-03: "I have it". The scan is recorded even when the order can't be confirmed.

        SL-07: from the seller's own browser it is a preview: recorded, never counted, and the
        order is not confirmed.
        """
        order = _seal_order(s, token)
        if _from_seller_browser(request, order):
            _preview_scan(s, order, "seal.preview_confirm")
            return _seal_view(s, order, seller_browser=True)
        try:
            record_scan(s, order, deps.clock.now())
        except SealError as exc:
            raise HTTPException(409, str(exc)) from None
        try:
            _pipeline(s).confirm_received(order, actor="buyer_seal")
        except BuyerActionError:
            pass  # e.g. a case is already open: the scan still stands as evidence
        s.commit()
        return _seal_view(s, order)

    @router.post("/api/seal/{token}/report", response_model=ReportOut)
    def seal_report(token: str, body: SealReportIn, request: Request, s: Db) -> ReportOut:
        """SL-06: "Something's wrong" from the same scan: the buyer holds the parcel.

        SL-07: the seller's own browser can't open a case as the buyer (403, scan recorded).
        """
        order = _seal_order(s, token)
        if _from_seller_browser(request, order):
            _preview_scan(s, order, "seal.preview_report")
            raise HTTPException(403, "seller preview: only the buyer's phone can report")
        try:
            record_scan(s, order, deps.clock.now())
            report = _pipeline(s).report(order, ClaimReason(body.reason), reported_by=BUYER)
        except (SealError, BuyerActionError) as exc:
            s.rollback()
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return ReportOut(
            case_id=report.case_id,
            status=report.route.value,
            message=report.buyer_message,
            rights=_rights(order),
        )

    @router.post("/api/buyer/{token}/agent-link", response_model=AgentLinkOut)
    def agent_link(token: str, s: Db) -> AgentLinkOut:
        """MC-01: a separate token, scoped to this one order, for the buyer's AI assistant."""
        order = _order(s, token)
        agent_token = services.links.issue(order.id, scope=MCP_SCOPE, ttl_s=AGENT_LINK_TTL_S)
        return AgentLinkOut(
            order_token=agent_token,
            mcp_url=f"{services.app_base_url}{MCP_MOUNT}{MCP_PATH}",
            tool="report_delivery_problem",
            expires_in_days=AGENT_LINK_TTL_S // 86400,
        )

    @router.post("/api/buyer/{token}/choice", response_model=BuyerView)
    def choice(token: str, body: ChoiceIn, s: Db) -> BuyerView:
        order = _order(s, token)
        try:
            ShippingService(s, deps.clock, deps.settings).choose(order, body.option)
        except ShippingError as exc:
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return buyer_view(s, order)

    # ------------------------------------------------------------ seller

    def _seller_order(s: Session, seller: Seller, order_id: str) -> Order:
        order = s.get(Order, order_id)
        if order is None or order.seller_id != seller.id:
            raise HTTPException(404, "not found")
        return order

    def _seller_case(s: Session, seller: Seller, case_id: str) -> Case:
        case = s.get(Case, case_id)
        if case is None or case.seller_id != seller.id:
            raise HTTPException(404, "not found")
        return case

    @router.get("/api/seller/{token}/orders", response_model=list[OrderRow])
    def orders(token: str, s: Db) -> list[OrderRow]:
        seller = _seller(s, token)
        rows = s.scalars(
            select(Order).where(Order.seller_id == seller.id).order_by(Order.created_at.desc())
        )
        return [order_row(s, o) for o in rows]

    @router.get("/api/seller/{token}/me")
    def me(token: str, response: Response, s: Db) -> dict[str, Any]:
        seller = _seller(s, token)
        _mark_seller_browser(response, seller)
        return {"seller_id": seller.id, "name": seller.name, "region": seller.region}

    @router.get("/api/seller/{token}/orders/{order_id}/buyer-link")
    def buyer_link(token: str, order_id: str, s: Db) -> dict[str, str]:
        order = _seller_order(s, _seller(s, token), order_id)
        buyer_token = services.links.issue(order.id, scope=BUYER)
        return {"buyer_token": buyer_token, "buyer_url": _buyer_url(buyer_token)}

    @router.get("/api/seller/{token}/orders/{order_id}/seal", response_model=SealCardOut)
    def seal_card(token: str, order_id: str, response: Response, s: Db) -> SealCardOut:
        """SL-01: the seal to print inside the parcel (same URL works on an NFC sticker)."""
        seller = _seller(s, token)
        order = _seller_order(s, seller, order_id)
        _mark_seller_browser(response, seller)
        url = seal_url(services.app_base_url, issue_seal(services.links, order.id))
        return SealCardOut(order_ref=order.id[:8].upper(), url=url, svg=seal_svg(url))

    @router.get("/api/seller/{token}/cases/{case_id}", response_model=CaseView)
    def case_page(token: str, case_id: str, s: Db) -> CaseView:
        return case_view(s, _seller_case(s, _seller(s, token), case_id))

    @router.get("/api/seller/{token}/cases/{case_id}/recovery", response_model=RecoveryView)
    def recovery_page(token: str, case_id: str, s: Db) -> RecoveryView:
        case = _seller_case(s, _seller(s, token), case_id)
        recovery = case_recovery(s, case.id)
        if recovery is None:
            raise HTTPException(404, "no carrier claim for this case")
        return recovery_view(recovery)

    # RC-08: one per process, so two opens at once make one model call.
    claim_drafts = ClaimDraftOnOpen(RecoveryDrafter(services.agent_llm), deps.clock)

    @router.post("/api/seller/{token}/cases/{case_id}/recovery/draft", response_model=RecoveryView)
    def recovery_draft(token: str, case_id: str, s: Db) -> RecoveryView:
        """RC-08: the model drafts the claim pack when the seller first opens it, then saved.
        Registered before the {mark} route, so "draft" is never read as a seller mark."""
        recovery = case_recovery(s, _seller_case(s, _seller(s, token), case_id).id)
        if recovery is None:
            raise HTTPException(404, "no carrier claim for this case")
        return recovery_view(claim_drafts.draft(s, recovery))

    @router.post("/api/seller/{token}/cases/{case_id}/recovery/{mark}", response_model=RecoveryView)
    def recovery_mark(
        token: str, case_id: str, mark: SellerMark, s: Db, body: RecoveryMarkIn | None = None
    ) -> RecoveryView:
        """RC-05: Submitted / Carrier paid (amount) / Carrier refused / Withdraw, one tap each."""
        seller = _seller(s, token)
        recovery = case_recovery(s, _seller_case(s, seller, case_id).id)
        if recovery is None:
            raise HTTPException(404, "no carrier claim for this case")
        try:
            seller_mark(
                s,
                recovery,
                mark,
                actor=f"seller:{seller.id}",
                now=deps.clock.now(),
                amount_minor=body.amount_minor if body else None,
            )
        except RecoveryError as exc:
            s.rollback()
            raise HTTPException(422, str(exc)) from None
        except IllegalTransition as exc:
            s.rollback()
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return recovery_view(recovery)

    @router.get("/api/seller/{token}/recoveries", response_model=RecoverySummary)
    def recoveries(token: str, s: Db) -> RecoverySummary:
        seller = _seller(s, token)
        recovered: dict[str, int] = {}
        open_minor: dict[str, int] = {}
        open_claims = 0
        for r in s.scalars(select(Recovery).where(Recovery.seller_id == seller.id)):
            if r.status == "PAID":
                recovered[r.currency] = recovered.get(r.currency, 0) + (r.recovered_minor or 0)
            elif r.status in ("PREPARED", "SUBMITTED"):
                open_claims += 1
                open_minor[r.currency] = open_minor.get(r.currency, 0) + r.claim_minor
        return RecoverySummary(
            recovered_minor=recovered, open_claims=open_claims, open_claim_minor=open_minor
        )

    @router.get("/api/seller/{token}/insights", response_model=InsightsOut)
    def insight_tables(token: str, s: Db) -> InsightsOut:
        """AS-01: orders, shipments, cases, money actions and carrier claims for AG Studio."""
        return insights(s, _seller(s, token))

    @router.post("/api/seller/{token}/insights/ask", response_model=InsightAnswer)
    def ask_insights(token: str, body: InsightQuestionIn, s: Db) -> InsightAnswer:
        """AS-06: Ask Doorstep reads one question into a chart plan. Reads nothing else; moves
        nothing. The browser builds the Studio widget and computes the figures from its rows."""
        _seller(s, token)
        return ask_doorstep.ask(body.question)

    @router.get("/api/seller/{token}/prevention", response_model=list[PreventionSuggestion])
    def prevention_tips(token: str, s: Db) -> list[PreventionSuggestion]:
        """CF-02: suggestions from the seller's own seller-liable cases, computed in code."""
        return prevention(s, _seller(s, token).id)

    @router.post("/api/seller/{token}/cases/{case_id}/{action}", response_model=ActionOut)
    def act(
        token: str,
        case_id: str,
        action: Literal["approve", "decline", "send-evidence"],
        s: Db,
    ) -> ActionOut:
        """approve / decline, or (DE-01) send the facts-only evidence to the open PayPal dispute."""
        seller = _seller(s, token)
        case = _seller_case(s, seller, case_id)
        actions = CaseActions(s, rulebook, deps.clock, services.receipts)
        verify_url = f"{services.app_base_url}/cases/{case.id}/verify"
        try:
            money = actions.act(case, action, seller_approver(seller.id), verify_url=verify_url)
        except (PolicyError, IllegalTransition, UnsafeEvidence) as exc:
            s.rollback()
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return ActionOut(
            case_id=case.id, state=case.state, money_action_id=money.id if money else None
        )

    recent_runs: dict[str, tuple[float, str, AgentRun]] = {}  # case id -> (when, state, run)

    @router.post("/api/seller/{token}/agent/cases/{case_id}", response_model=AgentRun)
    def run_agent(token: str, case_id: str, s: Db) -> AgentRun:
        """AT-01..05: the Doorstep agent reads the PayPal side and asks the policy; it stops
        at the seller's limit. Seeded demo orders use simulated reads. AT-06: a run on the same
        case in the same state within AGENT_REUSE_S is reused, so opening a case twice never
        repeats the PayPal reads or the timeline rows. AT-07: the automatic pre-check and the
        button share that run, so an opened case costs at most one planned run."""
        case = _seller_case(s, _seller(s, token), case_id)
        order = s.get(Order, case.order_id)
        assert order is not None
        now = deps.clock.monotonic()
        last = recent_runs.get(case.id)
        if last is not None and last[1] == case.state and now - last[0] <= AGENT_REUSE_S:
            return last[2]
        real = services.agent_tools
        tools = real if real is not None and not is_simulated(order.capture_id) else None
        agent = DoorstepAgent(tools or simulated_tools(s, order), llm=services.agent_llm)
        run = agent.run(s, case, clock=deps.clock)
        s.commit()
        recent_runs[case.id] = (now, case.state, run)
        return run

    @router.post("/api/seller/{token}/policy/cases/{case_id}", response_model=ReaderAnswer)
    def policy_answer(
        token: str, case_id: str, s: Db, body: PolicyQuestionIn | None = None
    ) -> ReaderAnswer:
        """RG-01/02: what the seller's policy and the applying rule say about this case, with
        cited passages (the rule file is quoted when no passage is relevant)."""
        seller = _seller(s, token)
        case = _seller_case(s, seller, case_id)
        order = s.get(Order, case.order_id)
        assert order is not None
        question = (body.question if body and body.question else None) or _REASON_QUESTION.get(
            case.reason, "What does the delivery policy say?"
        )
        reader = PolicyReader(services.agent_llm)
        return reader.answer(
            question, seller_policy=seller.policy_text, rule=rulebook.select(order.buyer_country)
        )

    @router.post("/api/seller/{token}/orders/{order_id}/ship", response_model=OrderRow)
    def ship(token: str, order_id: str, body: ShipIn, s: Db) -> OrderRow:
        order = _seller_order(s, _seller(s, token), order_id)
        try:
            ShippingService(s, deps.clock, deps.settings).ship(
                order,
                carrier=body.carrier,
                tracking_number=body.tracking_number,
                label_weight_g=body.label_weight_g,
            )
        except ShippingError as exc:
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return order_row(s, order)

    def _packing(s: Session) -> PackingService:
        return PackingService(s, deps.clock, services.packing or PackingChecker(None))

    @router.post("/api/seller/{token}/orders/{order_id}/packing-check", response_model=PackingOut)
    def packing_check(token: str, order_id: str, body: PackingIn, s: Db) -> PackingOut:
        """PK-01..04: compare one packing photo with the order line before shipping."""
        order = _seller_order(s, _seller(s, token), order_id)
        if body.media_type not in _PHOTO_TYPES:
            raise HTTPException(415, "send a JPEG, PNG or WebP photo")
        try:
            data = base64.b64decode(body.image_b64, validate=True)
        except binascii.Error:
            raise HTTPException(422, "image_b64 is not valid base64") from None
        if not data or len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(413, "the photo must be under 5 MB")
        try:
            verdict = _packing(s).check(
                order, Image(media_type=body.media_type, data=data), actor="seller"
            )
        except PackingError as exc:
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return PackingOut(
            verdict=verdict.verdict.value,
            attribute=verdict.attribute,
            message=verdict.message,
            reasons=verdict.result.reasons if verdict.result else [],
            order=order_row(s, order),
        )

    @router.post("/api/seller/{token}/orders/{order_id}/packing-clear", response_model=OrderRow)
    def packing_clear(token: str, order_id: str, s: Db) -> OrderRow:
        """The seller looked and confirms the item is right; recorded for the engine."""
        seller = _seller(s, token)
        order = _seller_order(s, seller, order_id)
        try:
            _packing(s).clear(order, actor=f"seller:{seller.id}")
        except PackingError as exc:
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return order_row(s, order)

    # ------------------------------------------------------------ demo

    def _seed(s: Session, tag: str) -> SeedOut:
        summary = seed_scenario(
            s,
            rulebook=rulebook,
            clock=deps.clock,
            settings=deps.settings,
            signer=services.receipts,
            tag=tag,
        )
        s.commit()
        seller_token = services.links.issue(summary.seller_id, scope=SELLER)
        buyers = {o.key: services.links.issue(o.order_id, scope=BUYER) for o in summary.orders}
        return SeedOut(
            tag=tag,
            seller_token=seller_token,
            seller_url=_seller_url(seller_token),
            buyers=buyers,
            summary=summary,
        )

    @router.post("/demo/seed", response_model=SeedOut)
    def demo_seed(s: Db, scratch: Annotated[int, Query(ge=0, le=1)] = 1) -> SeedOut:
        """PR-06: scratch=1 gives a fresh isolated copy; scratch=0 returns the filmed demo."""
        import uuid

        if not scratch and services.environment != "development":
            # The filmed demo's links are printed by `make demo`; a public caller must never
            # be able to approve or decline the cases shown in the video.
            raise HTTPException(403, "the filmed demo is not public; use scratch=1")
        tag = uuid.uuid4().hex[:12] if scratch else DEMO_TAG
        return _seed(s, tag)

    @router.post("/demo/reset")
    def demo_reset(body: ResetIn, s: Db) -> dict[str, Any]:
        """PR-07: development only, and only with the exact confirmation phrase."""
        if services.environment != "development":
            raise HTTPException(403, "reset is disabled outside development")
        if body.confirm != services.reset_phrase:
            raise HTTPException(400, f"type the phrase exactly: {services.reset_phrase}")
        for table in reversed(Base.metadata.sorted_tables):
            s.execute(table.delete())
        s.commit()
        return {"reset": True, "seed": _seed(s, DEMO_TAG).model_dump(mode="json")}

    return router
