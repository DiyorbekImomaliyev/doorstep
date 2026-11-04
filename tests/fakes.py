"""Offline fakes: clock, PayPal (both gateways), webhook verifier. No network."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.money import Money
from app.orders.enums import ShippingMethod
from app.orders.schemas import Address
from app.paypal_client.types import (
    CaptureResult,
    CaptureState,
    CreatedOrder,
    DisputeEvidence,
    RefundResult,
    ShippingOption,
)

START = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: datetime = START) -> None:
        self._now = start
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._mono += seconds


@dataclass
class FakeRefund:
    request_id: str | None
    amount_minor: int
    refund_id: str
    external: bool = False


@dataclass
class FakeCapture:
    paypal_order_id: str
    capture_id: str
    amount_minor: int
    currency: str
    status: str = "COMPLETED"
    refunds: list[FakeRefund] = field(default_factory=list)

    @property
    def refunded_minor(self) -> int:
        return sum(r.amount_minor for r in self.refunds)


class FakePayPal:
    """Implements RefundGateway (app.policy.ports) and OrdersGateway (app.worker.gateway).

    Deliberately does NOT reject over-refunds, so property tests catch our own bugs.
    Refunds are idempotent on request_id, like PayPal.
    """

    def __init__(self) -> None:
        self.captures: dict[str, FakeCapture] = {}
        self.refund_calls: list[tuple[str, int | None, str]] = []
        self.capture_calls: list[tuple[str, str]] = []
        self.tracking_calls: list[dict[str, str]] = []
        self.address_calls: list[tuple[str, Address, str]] = []
        self.evidence_calls: list[tuple[str, DisputeEvidence, str]] = []
        self.refund_failures: deque[Exception] = deque()
        self.capture_results: deque[CaptureResult | Exception] = deque()
        self.tracking_failures: deque[Exception] = deque()
        self._next = 0

    # ---- setup helpers -------------------------------------------------
    def add_capture(self, paypal_order_id: str, capture_id: str, amount: Money) -> None:
        self.captures[capture_id] = FakeCapture(
            paypal_order_id, capture_id, amount.amount_minor, amount.currency
        )

    def external_refund(self, capture_id: str, amount_minor: int) -> str:
        """A refund the seller made in the PayPal dashboard, outside Doorstep."""
        refund_id = self._new_id("EXT")
        self.captures[capture_id].refunds.append(
            FakeRefund(None, amount_minor, refund_id, external=True)
        )
        return refund_id

    def fail_next_refunds(self, *errors: Exception) -> None:
        self.refund_failures.extend(errors)

    def total_refunded(self, capture_id: str) -> int:
        return self.captures[capture_id].refunded_minor

    def _new_id(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next}"

    # ---- RefundGateway -------------------------------------------------
    def get_capture_state(self, paypal_order_id: str, capture_id: str) -> CaptureState:
        cap = self.captures[capture_id]
        refunded = cap.refunded_minor
        if refunded == 0:
            status = cap.status
        elif refunded >= cap.amount_minor:
            status = "REFUNDED"
        else:
            status = "PARTIALLY_REFUNDED"
        return CaptureState(
            capture_id=capture_id,
            status=status,
            amount=Money(amount_minor=cap.amount_minor, currency=cap.currency),
            refunded_total=Money(amount_minor=refunded, currency=cap.currency),
        )

    def refund_capture(
        self, capture_id: str, *, amount: Money | None, request_id: str
    ) -> RefundResult:
        self.refund_calls.append(
            (capture_id, None if amount is None else amount.amount_minor, request_id)
        )
        if self.refund_failures:
            raise self.refund_failures.popleft()
        cap = self.captures[capture_id]
        for r in cap.refunds:
            if r.request_id == request_id:
                return RefundResult(refund_id=r.refund_id, status="COMPLETED")
        minor = cap.amount_minor - cap.refunded_minor if amount is None else amount.amount_minor
        refund = FakeRefund(request_id, minor, self._new_id("R"))
        cap.refunds.append(refund)
        return RefundResult(
            refund_id=refund.refund_id, status="COMPLETED", raw={"id": refund.refund_id}
        )

    def provide_dispute_evidence(
        self, dispute_id: str, *, evidence: DisputeEvidence, request_id: str
    ) -> None:
        self.evidence_calls.append((dispute_id, evidence, request_id))

    # ---- OrdersGateway -------------------------------------------------
    def create_order(
        self,
        *,
        amount: Money,
        options: Sequence[ShippingOption],
        selected: ShippingMethod,
        request_id: str,
    ) -> CreatedOrder:
        oid = self._new_id("PPO")
        return CreatedOrder(order_id=oid, approve_url=f"https://sandbox.paypal.test/approve/{oid}")

    def update_shipping_address(
        self, paypal_order_id: str, address: Address, *, request_id: str
    ) -> None:
        self.address_calls.append((paypal_order_id, address, request_id))

    def capture_order(self, paypal_order_id: str, *, request_id: str) -> CaptureResult:
        self.capture_calls.append((paypal_order_id, request_id))
        result = self.capture_results.popleft()
        if isinstance(result, Exception):
            raise result
        return result

    def add_tracking(
        self,
        *,
        paypal_order_id: str,
        capture_id: str,
        carrier: str,
        tracking_number: str,
        request_id: str,
    ) -> None:
        self.tracking_calls.append(
            {
                "paypal_order_id": paypal_order_id,
                "capture_id": capture_id,
                "carrier": carrier,
                "tracking_number": tracking_number,
                "request_id": request_id,
            }
        )
        if self.tracking_failures:
            raise self.tracking_failures.popleft()


class FakeVerifier:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls = 0

    def verify(self, headers: Mapping[str, str], event: dict[str, Any]) -> bool:
        self.calls += 1
        return self.result
