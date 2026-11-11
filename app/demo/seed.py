"""The Diyorbek and Malika demo scenario (E2E-02) and per-judge scratch copies (PR-06).

Every row is built the way production builds it: orders start CREATED and move only through
app.orders.state; cases run through the real CasePipeline (engine -> policy -> explainer ->
receipt). Ids are uuid5 of (tag, key), so a seed is identical every time for the same tag.

Seeded orders never went through PayPal checkout: their capture ids start with `SIM-`, so any
refund on them is routed to the simulated gateway and labelled as simulated (PR-05, PR-08).
The filmed refund comes from a real sandbox checkout, not from seeded data.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.explainer import Explainer
from app.cases.actions import CaseActions, seller_approver
from app.cases.engine import ClaimReason
from app.cases.pipeline import CasePipeline
from app.cases.signals import EvidenceKind
from app.clock import Clock
from app.config import Settings
from app.orders.enums import OrderState
from app.orders.models import (
    AuditLog,
    Case,
    Dispute,
    Evidence,
    Order,
    Product,
    Recovery,
    Seller,
    Shipment,
    TrackingEvent,
)
from app.orders.state import transition_order
from app.policy.rules import RuleBook
from app.proof.receipts import ReceiptSigner
from app.proof.seal import record_scan
from app.shipping.service import FAILED_ATTEMPT_CHOICES
from app.worker.simulated import SIM_PREFIX

DEMO_TAG = "demo"
DEMO_SKU = "jacket-black-m"  # CK-07: the one item Malika's demo shop sells, priced on the server
_DEMO_PRICES = {"GBP": 6000, "USD": 6000}


def _add_demo_product(session: Session, seller_id: str) -> None:
    session.add(
        Product(seller_id=seller_id, sku=DEMO_SKU, name="Black jacket, size M", prices=_DEMO_PRICES)
    )


def backfill_demo_products(session: Session) -> int:
    """Gives every demo shop seeded before CK-07 its product; real sellers are never touched."""
    has_product = select(Product.id).where(Product.seller_id == Seller.id).exists()
    missing = session.scalars(
        select(Seller.id).where(Seller.scratch_tag.is_not(None), ~has_product)
    ).all()
    for seller_id in missing:
        _add_demo_product(session, seller_id)
    session.flush()
    return len(missing)


def backfill_demo_catalog(engine: Engine) -> None:
    """Startup step. The web service and the worker may race; the loser logs and moves on."""
    try:
        with Session(engine) as session:
            backfill_demo_products(session)
            session.commit()
    except SQLAlchemyError:
        logging.getLogger(__name__).warning("demo product backfill skipped")


_NS = uuid.UUID("7c1d6a52-4c1e-4a59-9a43-6a3d0f1d2c11")

_O = OrderState
_PATHS: dict[OrderState, list[OrderState]] = {
    _O.PAID: [_O.PAID],
    _O.IN_TRANSIT: [_O.PAID, _O.SHIPPED, _O.IN_TRANSIT],
    _O.EXCEPTION: [_O.PAID, _O.SHIPPED, _O.IN_TRANSIT, _O.EXCEPTION],
    _O.DELIVERED: [_O.PAID, _O.SHIPPED, _O.IN_TRANSIT, _O.DELIVERED],
    _O.PICKED_UP: [_O.PAID, _O.SHIPPED, _O.IN_TRANSIT, _O.PICKED_UP],
}
_CARRIER_STATUS = {
    _O.SHIPPED: "PRE_TRANSIT",
    _O.IN_TRANSIT: "TRANSIT",
    _O.EXCEPTION: "FAILURE",
    _O.DELIVERED: "DELIVERED",
    _O.PICKED_UP: "DELIVERED",
}


@dataclass(frozen=True)
class _Spec:
    key: str
    buyer: str
    item: str
    amount_minor: int
    currency: str
    country: str
    method: str
    state: OrderState
    postcode: str
    city: str
    hours_ago: int
    evidence: tuple[tuple[EvidenceKind, dict[str, Any]], ...] = ()
    report: ClaimReason | None = None
    confirm: bool = False
    stale: bool = False
    failure_substatus: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    # v2 liability router (simulated carrier data, labelled in /healthz):
    carrier: str = "shippo"
    delivered_zip: str | None = None  # where the carrier says it delivered (SA-02)
    delivered_offset_s: int = 0  # seconds after the computed delivered time (batch scans, SA-03)
    seal_scan_after_h: int | None = None  # the buyer scanned the seal this long after delivery
    dispute: bool = False  # the buyer opened a (simulated) PayPal dispute before reporting
    approved: bool = False  # Malika already approved this refund (AP-01 precedent)
    carrier_paid: bool = False  # RC-09: the carrier already paid Malika's claim for this one
    arrives_after_report: bool = False  # AD-01: the courier scans it delivered after the report


_PACKED = (EvidenceKind.PACKING_PHOTO, {"verdict": "match", "expected_weight_g": 900})

SCENARIO: tuple[_Spec, ...] = (
    # Paid, not yet packed: the packing-photo check scene (PK) starts here.
    _Spec(
        "to-pack",
        "Nilufar Rakhimova",
        "Black jacket, size L",
        6000,
        "GBP",
        "GB",
        "locker",
        _O.PAID,
        "M1 1AE",
        "Manchester",
        2,
    ),
    # Royal Mail says "Delivered", but its own scan came from E8, in the same minute as two of
    # Malika's other parcels to other districts (simulated carrier data, SA-02/SA-03).
    _Spec(
        "sam-jacket",
        "Diyorbek Karimov",
        "Black jacket, size M",
        6000,
        "GBP",
        "GB",
        "home",
        _O.DELIVERED,
        "N1 9GU",
        "London",
        30,
        (_PACKED,),
        carrier="royal_mail",
        delivered_zip="E8 3RL",
    ),
    _Spec(
        "failed-attempt",
        "Dilnoza Yusupova",
        "Wool scarf, grey",
        3500,
        "GBP",
        "GB",
        "home",
        _O.EXCEPTION,
        "M14 5RT",
        "Manchester",
        20,
        (_PACKED,),
        failure_substatus="no_access",
    ),
    _Spec(
        "locker-pickup",
        "Jasur Nazarov",
        "Leather boots, size 8",
        4500,
        "GBP",
        "GB",
        "locker",
        _O.PICKED_UP,
        "LS6 2AB",
        "Leeds",
        50,
        (_PACKED, (EvidenceKind.LOCKER_CODE, {"code_used": True})),
    ),
    _Spec(
        "empty-box",
        "Aziz Saidov",
        "Bucket hat, black",
        2800,
        "GBP",
        "GB",
        "home",
        _O.DELIVERED,
        "BS1 4DJ",
        "Bristol",
        70,
        (_PACKED,),
        report=ClaimReason.EMPTY_BOX,
    ),
    _Spec(
        "damaged-mug",
        "Kamola Ergasheva",
        "Ceramic mug set",
        4000,
        "GBP",
        "GB",
        "home",
        _O.DELIVERED,
        "CF10 1EP",
        "Cardiff",
        60,
        (_PACKED, (EvidenceKind.BUYER_PHOTO, {"damage_visible": True, "severity": "major"})),
        report=ClaimReason.DAMAGED,
    ),
    _Spec(
        "in-transit",
        "Bekzod Tursunov",
        "Knit gloves",
        2200,
        "GBP",
        "GB",
        "locker",
        _O.IN_TRANSIT,
        "EH1 1YZ",
        "Edinburgh",
        10,
        (_PACKED,),
    ),
    _Spec(
        "lost-denim",
        "Madina Aliyeva",
        "Denim jacket, size S",
        7500,
        "GBP",
        "GB",
        "home",
        _O.IN_TRANSIT,
        "B1 1AA",
        "Birmingham",
        120,
        (_PACKED,),
        report=ClaimReason.NOT_RECEIVED,
        stale=True,
    ),
    _Spec(
        "signed-coat",
        "Sardor Usmonov",
        "Wool coat, navy, size L",
        12000,
        "GBP",
        "GB",
        "signature",
        _O.DELIVERED,
        "SW1A 1AA",
        "London",
        90,
        (_PACKED, (EvidenceKind.SIGNATURE, {"signed_by": "_O. Haddad"})),
        confirm=True,
    ),
    _Spec(
        "us-sneakers",
        "Shahnoza Mirzaeva",
        "Running sneakers, US 10",
        12000,
        "USD",
        "US",
        "home",
        _O.DELIVERED,
        "94107",
        "San Francisco",
        40,
        (_PACKED,),
    ),
    # The two other parcels in Diyorbek's batch scan: same van, same minute, other districts.
    _Spec(
        "batch-lamp",
        "Sevara Hamidova",
        "Brass desk lamp",
        3800,
        "GBP",
        "GB",
        "home",
        _O.DELIVERED,
        "N16 7UX",
        "London",
        30,
        (_PACKED,),
        carrier="royal_mail",
        delivered_zip="E8 3RL",
        delivered_offset_s=14,
    ),
    _Spec(
        "batch-tee",
        "Otabek Sobirov",
        "Linen shirt, white, size M",
        3200,
        "GBP",
        "GB",
        "home",
        _O.DELIVERED,
        "E8 1DY",
        "London",
        30,
        (_PACKED,),
        carrier="royal_mail",
        delivered_zip="E8 3RL",
        delivered_offset_s=37,
    ),
    # Possession proven: the buyer scanned the seal inside the parcel, then opened a PayPal
    # dispute. PayPal decides; Malika sends the facts (DE-01..04).
    _Spec(
        "us-headphones",
        "Javohir Qodirov",
        "Wireless headphones",
        8900,
        "USD",
        "US",
        "home",
        _O.DELIVERED,
        "10001",
        "New York",
        80,
        (_PACKED,),
        report=ClaimReason.NOT_RECEIVED,
        carrier="usps",
        delivered_zip="10001",
        seal_scan_after_h=2,
        dispute=True,
    ),
    # AP-07: delivered, no case yet. After Malika turns autopilot on, Dilnoza's "Not here" is
    # refunded by her rule without waiting for her (over the £50 limit, within her £60 rule).
    _Spec(
        "priya-beret",
        "Dilnoza Yusupova",
        "Wool beret, burgundy",
        5200,
        "GBP",
        "GB",
        "home",
        _O.DELIVERED,
        "M14 5RG",
        "Manchester",
        26,
        (_PACKED,),
        carrier="evri",  # not Royal Mail, so it never joins the filmed batch-scan story (SA-03)
    ),
    # Malika's week (AP-01): two lost parcels over her £50 limit that she approved herself. Kept no
    # older than the other demo orders, so Insights' simulated history still ends where it did.
    # After she approves Diyorbek's £60 refund, Doorstep offers to handle these for her.
    _Spec(
        "past-scarf",
        "Sherzod Abdullaev",
        "Silk scarf, green",
        5500,
        "GBP",
        "GB",
        "home",
        _O.DELIVERED,
        "LS1 4AP",
        "Leeds",
        68,
        (_PACKED,),
        report=ClaimReason.NOT_RECEIVED,
        carrier="royal_mail",
        delivered_zip="LS9 8AB",
        approved=True,
    ),
    _Spec(
        "past-belt",
        "Zarina Islomova",
        "Leather belt, brown",
        5800,
        "GBP",
        "GB",
        "home",
        _O.DELIVERED,
        "B1 1BB",
        "Birmingham",
        52,
        (_PACKED,),
        report=ClaimReason.NOT_RECEIVED,
        carrier="royal_mail",
        delivered_zip="B12 0XS",
        approved=True,
        carrier_paid=True,
    ),
    # AD-01: tracking went quiet, Malika refunded it as lost, then the courier scanned it delivered.
    _Spec(
        "late-trousers",
        "Gulnora Rashidova",
        "Cord trousers, olive, W32",
        4600,
        "GBP",
        "GB",
        "home",
        _O.IN_TRANSIT,
        "M14 5RX",
        "Manchester",
        110,
        (_PACKED,),
        report=ClaimReason.NOT_RECEIVED,
        stale=True,
        carrier="royal_mail",
        approved=True,
        arrives_after_report=True,
    ),
)


class SeededOrder(BaseModel):
    key: str
    order_id: str
    buyer: str
    item: str
    state: str
    case_id: str | None = None
    route: str | None = None


class SeedSummary(BaseModel):
    tag: str
    seller_id: str
    orders: list[SeededOrder]


def _id(tag: str, key: str) -> str:
    return str(uuid.uuid5(_NS, f"{tag}:{key}"))


def _backdate_last_transition(session: Session, order_id: str, at: datetime) -> None:
    """Seeded history happened in the past: date its audit row like its carrier event.

    Only the demo seed does this, inside the transaction that writes the row, so the simulated
    timeline reads in order. Real transitions keep the time they actually happened.
    """
    row = session.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "order", AuditLog.entity_id == order_id)
        .where(AuditLog.actor == "demo-seed", AuditLog.at > at)
        .order_by(AuditLog.at.desc())
    ).first()
    if row is not None:
        row.at = at


def seed_scenario(
    session: Session,
    *,
    rulebook: RuleBook,
    clock: Clock,
    settings: Settings,
    signer: ReceiptSigner,
    tag: str = DEMO_TAG,
) -> SeedSummary:
    """Seeds Malika's shop for `tag`. Seeding a tag that already exists returns it unchanged."""
    seller_id = _id(tag, "seller")
    if session.get(Seller, seller_id) is None:
        _build(session, rulebook, clock, settings, signer, tag, seller_id)
    return _summary(session, tag, seller_id)


def _build(
    session: Session,
    rulebook: RuleBook,
    clock: Clock,
    settings: Settings,
    signer: ReceiptSigner,
    tag: str,
    seller_id: str,
) -> None:
    now = clock.now()
    session.add(
        Seller(
            id=seller_id,
            name="Malika's Vintage",
            region="GB",
            scratch_tag=tag,
            policy_text=(
                "We refund or reship any order that does not reach you. Once tracking shows your "
                "parcel as delivered, it is your responsibility. Report a problem within 30 days "
                "of the expected delivery date."
            ),
        )
    )
    session.flush()
    _add_demo_product(session, seller_id)
    # RC-08: seeded cases keep the fact-only claim pack; every judge reset would otherwise ask the
    # model to reword about nine packs nobody opened. Cases reported during the demo get the draft.
    pipeline = CasePipeline(
        session, rulebook, clock, settings, Explainer(), signer, ai_claims=False
    )
    actions = CaseActions(session, rulebook, clock, signer)
    for spec in SCENARIO:
        oid = _id(tag, spec.key)
        created = now - timedelta(hours=spec.hours_ago)
        order = Order(
            id=oid,
            seller_id=seller_id,
            paypal_order_id=f"{SIM_PREFIX}PPO-{oid[:13]}",
            capture_id=f"{SIM_PREFIX}CAP-{oid[:13]}",
            capture_status="COMPLETED",
            amount_minor=spec.amount_minor,
            currency=spec.currency,
            buyer_country=spec.country,
            payer_id=f"SIM-PAYER-{spec.key}",
            shipping_address={
                "name": spec.buyer,
                "line1": "Flat 1, 1 Demo Street" if spec.country == "GB" else "1 Demo Street",
                "city": spec.city,
                "postal_code": spec.postcode,
                "country": spec.country,
            },
            address_check="valid",
            shipping_method=spec.method,
            recommended_method=spec.method,
            item_description=spec.item,
            scratch_tag=tag,
            created_at=created,
        )
        session.add(order)
        session.flush()
        if spec.state == _O.PAID:  # not shipped yet: no shipment, no carrier events
            transition_order(session, order, _O.PAID, actor="demo-seed", reason="seeded PAID")
            _backdate_last_transition(session, oid, created + timedelta(minutes=5))
            continue
        shipment = Shipment(
            order_id=oid,
            carrier=spec.carrier,
            tracking_number=f"SIM-{oid[:8]}-{spec.key}".upper(),
            method=spec.method,
            label_weight_g=900,
            delivery_address=dict(order.shipping_address),
            created_at=created + timedelta(hours=2),
        )
        session.add(shipment)
        session.flush()
        for kind, result in spec.evidence:
            session.add(
                Evidence(order_id=oid, kind=kind.value, ai_result=result, created_at=created)
            )
        path = _PATHS[spec.state]
        step = timedelta(hours=max(1, spec.hours_ago // (len(path) + 1)))
        delivered_at: datetime | None = None
        for i, to in enumerate(path):
            at = created + step * (i + 1)
            if to == _O.DELIVERED:
                at += timedelta(seconds=spec.delivered_offset_s)
                delivered_at = at
            transition_order(session, order, to, actor="demo-seed", reason=f"seeded {to.value}")
            _backdate_last_transition(session, oid, at)
            status = _CARRIER_STATUS.get(to)
            if status is None or (spec.stale and to == _O.IN_TRANSIT):
                continue
            raw: dict[str, Any] = {"tracking_status": {"status": status}}
            if to == _O.EXCEPTION and spec.failure_substatus:
                raw["tracking_status"]["substatus"] = {"code": spec.failure_substatus}
            if to == _O.DELIVERED and spec.delivered_zip:
                raw["tracking_status"]["location"] = {
                    "zip": spec.delivered_zip,
                    "country": spec.country,
                }
            session.add(
                TrackingEvent(
                    shipment_id=shipment.id,
                    status=status,
                    occurred_at=at,
                    applied=True,
                    raw=raw,
                )
            )
        if spec.state == _O.EXCEPTION:
            shipment.choice_offered = list(FAILED_ATTEMPT_CHOICES)
        session.flush()
        if spec.seal_scan_after_h is not None and delivered_at is not None:
            record_scan(session, order, delivered_at + timedelta(hours=spec.seal_scan_after_h))
        if spec.dispute:
            session.add(
                Dispute(
                    order_id=oid,
                    paypal_dispute_id=f"{SIM_PREFIX}DISPUTE-{oid[:13]}",
                    status="open",
                    opened_at=(delivered_at or created) + timedelta(hours=6),
                )
            )
            session.flush()
        if spec.confirm:
            pipeline.confirm_received(order, actor="demo-seed")
        if spec.report is not None:
            report = pipeline.report(order, spec.report, reported_by="buyer")
            if spec.approved:
                case = session.get(Case, report.case_id)
                assert case is not None
                actions.act(case, "approve", seller_approver(seller_id))
                if spec.carrier_paid:
                    _carrier_paid(session, case.id, now)
            if spec.arrives_after_report:
                _arrive_after_report(session, order, shipment, report.case_id)
    session.flush()


def _carrier_paid(session: Session, case_id: str, now: datetime) -> None:
    """RC-09: Malika filed the carrier claim and was paid in full (through the recovery service,
    so the audit rows exist), so "Recovered from carriers" shows real money."""
    from app.cases.recovery import seller_mark

    recovery = session.scalars(select(Recovery).where(Recovery.case_id == case_id)).first()
    if recovery is None:
        return
    seller_mark(session, recovery, "submitted", actor="demo-seed", now=now - timedelta(days=3))
    seller_mark(
        session,
        recovery,
        "paid",
        actor="demo-seed",
        now=now - timedelta(days=1),
        amount_minor=recovery.claim_minor,
    )


def _arrive_after_report(session: Session, order: Order, shipment: Shipment, case_id: str) -> None:
    """AD-01: the courier's delivered scan lands just after the buyer's report."""
    from app.cases.late_delivery import record_delivered_scan

    case = session.get(Case, case_id)
    assert case is not None
    at = case.opened_at + timedelta(seconds=1)
    event = TrackingEvent(
        shipment_id=shipment.id,
        status="DELIVERED",
        occurred_at=at,
        applied=False,  # the case is open: the order's state stays where it is (SM-09)
        raw={"tracking_status": {"status": "DELIVERED"}},
    )
    session.add(event)
    session.flush()
    record_delivered_scan(session, order, event, now=at)


def _summary(session: Session, tag: str, seller_id: str) -> SeedSummary:
    from sqlalchemy import select

    from app.orders.models import Case, Decision

    out: list[SeededOrder] = []
    for spec in SCENARIO:
        order = session.get(Order, _id(tag, spec.key))
        assert order is not None
        case = session.scalars(
            select(Case).where(Case.order_id == order.id).order_by(Case.opened_at.desc())
        ).first()
        decision = (
            session.scalars(
                select(Decision)
                .where(Decision.case_id == case.id)
                .order_by(Decision.created_at.desc())
            ).first()
            if case
            else None
        )
        out.append(
            SeededOrder(
                key=spec.key,
                order_id=order.id,
                buyer=spec.buyer,
                item=spec.item,
                state=order.state,
                case_id=case.id if case else None,
                route=decision.route if decision else None,
            )
        )
    return SeedSummary(tag=tag, seller_id=seller_id, orders=out)
