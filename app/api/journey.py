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
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.explainer import Explainer
from app.ai.llm import Image
from app.ai.packing import PackingChecker
from app.api.agent import MCP_MOUNT, MCP_PATH
from app.api.deps import SIMULATED_COMPONENTS, AppDeps, Services
from app.api.views import (
    BuyerView,
    CaseView,
    OrderRow,
    buyer_view,
    case_view,
    order_row,
)
from app.cases.actions import CaseActions, seller_approver
from app.cases.engine import ClaimReason
from app.cases.pipeline import BuyerActionError, CasePipeline
from app.checkout.address import AddressChecker, AddressCheckResult
from app.checkout.service import CheckoutError, CheckoutService, DeliveryQuote
from app.db import Base
from app.demo.seed import DEMO_TAG, SeedSummary, seed_scenario
from app.mcp.server import MCP_SCOPE
from app.money import Currency, Money
from app.orders.enums import ShippingMethod
from app.orders.models import Case, Order, Seller
from app.orders.schemas import Address
from app.orders.state import IllegalTransition
from app.policy.rules import RuleBook
from app.policy.service import PolicyError
from app.proof.receipts import VerifyReport, verify_chain
from app.security.links import InvalidLink
from app.shipping.packing import PackingError, PackingService
from app.shipping.service import ShippingError, ShippingService

log = logging.getLogger("doorstep.api")

BUYER, SELLER = "buyer", "seller"


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


class ChoiceIn(BaseModel):
    option: str = Field(max_length=16)


class ShipIn(BaseModel):
    carrier: str = Field(default="shippo", max_length=64)
    tracking_number: str = Field(min_length=1, max_length=128)
    label_weight_g: int | None = Field(default=None, gt=0, le=100_000)


_PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_PHOTO_BYTES = 5 * 1024 * 1024


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


class Health(BaseModel):
    ok: bool
    environment: str
    rules: list[str]
    simulated: dict[str, str]
    receipts_public_key: str
    model: str


def build_journey_router(deps: AppDeps, services: Services, rulebook: RuleBook) -> APIRouter:
    router = APIRouter()
    explainer: Explainer = services.explainer or Explainer()

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

    def _buyer_url(token: str) -> str:
        return f"{services.app_base_url}/t/{token}"

    def _seller_url(token: str) -> str:
        return f"{services.app_base_url}/seller/{token}"

    # ------------------------------------------------------------ public

    @router.get("/healthz", response_model=Health)
    def healthz() -> Health:
        return Health(
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
        return buyer_view(s, _order(s, token))

    @router.post("/api/buyer/{token}/got-it", response_model=BuyerView)
    def got_it(token: str, s: Db) -> BuyerView:
        order = _order(s, token)
        try:
            _pipeline(s).confirm_received(order)
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
            case_id=report.case_id, status=report.route.value, message=report.buyer_message
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
    def me(token: str, s: Db) -> dict[str, Any]:
        seller = _seller(s, token)
        return {"seller_id": seller.id, "name": seller.name, "region": seller.region}

    @router.get("/api/seller/{token}/orders/{order_id}/buyer-link")
    def buyer_link(token: str, order_id: str, s: Db) -> dict[str, str]:
        order = _seller_order(s, _seller(s, token), order_id)
        buyer_token = services.links.issue(order.id, scope=BUYER)
        return {"buyer_token": buyer_token, "buyer_url": _buyer_url(buyer_token)}

    @router.get("/api/seller/{token}/cases/{case_id}", response_model=CaseView)
    def case_page(token: str, case_id: str, s: Db) -> CaseView:
        return case_view(s, _seller_case(s, _seller(s, token), case_id))

    @router.post("/api/seller/{token}/cases/{case_id}/{action}", response_model=ActionOut)
    def act(token: str, case_id: str, action: Literal["approve", "decline"], s: Db) -> ActionOut:
        seller = _seller(s, token)
        case = _seller_case(s, seller, case_id)
        actions = CaseActions(s, rulebook, deps.clock, services.receipts)
        try:
            money = actions.act(case, action, seller_approver(seller.id))
        except (PolicyError, IllegalTransition) as exc:
            s.rollback()
            raise HTTPException(409, str(exc)) from None
        s.commit()
        return ActionOut(
            case_id=case.id, state=case.state, money_action_id=money.id if money else None
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
