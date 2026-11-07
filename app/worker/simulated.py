"""Simulated refunds for judge scratch scenarios (PR-05, PR-06).

Scratch orders never went through a real PayPal checkout, so their capture ids start with
`SIM-`. RoutingRefundGateway sends those to SimulatedRefundGateway and every other capture to
the real PayPal gateway. Simulated refunds are labelled `SIMULATED` in their status, so no screen
can present them as real PayPal refunds (PR-08). `/healthz` lists this component as simulated.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.money import Currency, Money
from app.orders.enums import MoneyActionKind, MoneyActionStatus
from app.orders.models import Dispute, MoneyAction, Order
from app.paypal_client.types import (
    CaptureState,
    DisputeDetail,
    DisputeEvidence,
    DisputeSummary,
    RefundResult,
)
from app.policy.ports import DisputeLookup, OfferGateway, RefundGateway

SIM_PREFIX = "SIM-"
SIMULATED_STATUS = "SIMULATED_COMPLETED"


def is_simulated(capture_id: str | None) -> bool:
    return bool(capture_id) and str(capture_id).startswith(SIM_PREFIX)


class SimulatedRefundGateway:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def open_disputes_since(self, since: datetime, *, capture_id: str) -> list[DisputeSummary]:
        del since, capture_id  # a scratch order was never paid at PayPal, so it has no disputes
        return []

    def get_capture_state(self, paypal_order_id: str, capture_id: str) -> CaptureState:
        with self.session_factory() as s:
            order = s.scalars(select(Order).where(Order.capture_id == capture_id)).one()
            refunded = s.scalar(
                select(func.coalesce(func.sum(MoneyAction.amount_minor), 0)).where(
                    MoneyAction.order_id == order.id,
                    MoneyAction.status == MoneyActionStatus.COMPLETED.value,
                )
            )
            currency = Currency(order.currency)
            return CaptureState(
                capture_id=capture_id,
                status="COMPLETED",
                amount=Money(amount_minor=order.amount_minor, currency=currency),
                refunded_total=Money(amount_minor=int(refunded or 0), currency=currency),
            )

    def refund_capture(
        self,
        capture_id: str,
        *,
        amount: Money | None,
        request_id: str,
        custom_id: str | None = None,
    ) -> RefundResult:
        refund_id = "SIM-REFUND-" + hashlib.sha256(request_id.encode()).hexdigest()[:12].upper()
        return RefundResult(refund_id=refund_id, status=SIMULATED_STATUS, raw={"simulated": True})

    def provide_dispute_evidence(
        self, dispute_id: str, *, evidence: DisputeEvidence, request_id: str
    ) -> None:
        return None

    def get_dispute(self, dispute_id: str) -> DisputeDetail:
        """A seeded scratch dispute, read from our own rows: an inquiry that allows a refund
        offer, with an offer on record once one has completed (PP-09)."""
        with self.session_factory() as s:
            dispute = s.scalars(
                select(Dispute).where(Dispute.paypal_dispute_id == dispute_id)
            ).one()
            order = s.get(Order, dispute.order_id)
            assert order is not None
            offered = s.scalar(
                select(func.count()).where(
                    MoneyAction.order_id == order.id,
                    MoneyAction.kind == MoneyActionKind.OFFER.value,
                    MoneyAction.status == MoneyActionStatus.COMPLETED.value,
                )
            )
            return DisputeDetail(
                dispute_id=dispute_id,
                status="RESOLVED" if dispute.status != "open" else "WAITING_FOR_SELLER_RESPONSE",
                stage="INQUIRY",
                capture_ids=[order.capture_id] if order.capture_id else [],
                amount=Money(amount_minor=order.amount_minor, currency=Currency(order.currency)),
                offer_types=["REFUND"],
                seller_offer_made=bool(offered),
            )

    def make_dispute_offer(
        self, dispute_id: str, *, offer: Money, note: str, request_id: str
    ) -> None:
        return None


class RoutingRefundGateway:
    def __init__(self, real: RefundGateway, simulated: RefundGateway) -> None:
        self.real = real
        self.simulated = simulated

    def _for(self, capture_id: str) -> RefundGateway:
        return self.simulated if is_simulated(capture_id) else self.real

    def get_capture_state(self, paypal_order_id: str, capture_id: str) -> CaptureState:
        return self._for(capture_id).get_capture_state(paypal_order_id, capture_id)

    def open_disputes_since(self, since: datetime, *, capture_id: str) -> list[DisputeSummary]:
        target = self._for(capture_id)
        if not isinstance(target, DisputeLookup):  # fail closed: the refund job retries, then holds
            raise TypeError(f"{type(target).__name__} cannot read PayPal disputes")
        return target.open_disputes_since(since, capture_id=capture_id)

    def refund_capture(
        self,
        capture_id: str,
        *,
        amount: Money | None,
        request_id: str,
        custom_id: str | None = None,
    ) -> RefundResult:
        return self._for(capture_id).refund_capture(
            capture_id, amount=amount, request_id=request_id, custom_id=custom_id
        )

    def provide_dispute_evidence(
        self, dispute_id: str, *, evidence: DisputeEvidence, request_id: str
    ) -> None:
        # Seeded scratch disputes (ids starting SIM-) never reach PayPal (PR-05).
        self._for(dispute_id).provide_dispute_evidence(
            dispute_id, evidence=evidence, request_id=request_id
        )

    def _offers_for(self, dispute_id: str) -> OfferGateway:
        target = self._for(dispute_id)
        if not isinstance(target, OfferGateway):  # fail closed: the offer job fails, nothing sent
            raise TypeError(f"{type(target).__name__} cannot make dispute offers")
        return target

    def get_dispute(self, dispute_id: str) -> DisputeDetail:
        return self._offers_for(dispute_id).get_dispute(dispute_id)

    def make_dispute_offer(
        self, dispute_id: str, *, offer: Money, note: str, request_id: str
    ) -> None:
        # Seeded scratch disputes never reach PayPal (PR-05), offers included (PP-09).
        self._offers_for(dispute_id).make_dispute_offer(
            dispute_id, offer=offer, note=note, request_id=request_id
        )
