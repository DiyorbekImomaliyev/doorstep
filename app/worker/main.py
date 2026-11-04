"""Worker process: `python -m app.worker.main`. Processes the webhook inbox, runs due jobs
(PayPal captures, tracking, refunds; Shippo track registration) and sweeps stale tracking.

Each loop is small and idempotent, so a crash or a second worker is harmless (row locks with
SKIP LOCKED, unique dedupe keys, PayPal-Request-Id on every write).
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orders.enums import MoneyActionStatus
from app.orders.models import MoneyAction
from app.policy.rules import load_rules
from app.policy.service import REFUND_JOB, RefundJobHandler
from app.proof.receipts import ReceiptSigner, issue_receipt
from app.runtime import NotConfigured, Runtime, build_runtime
from app.shipping.events import handle_shippo_track_updated
from app.shipping.service import REGISTER_TRACK, RegisterTrackHandler, ShippingService
from app.worker.handlers import ADD_TRACKING, CAPTURE_ORDER, AddTrackingHandler, CaptureOrderHandler
from app.worker.inbox import InboxProcessor
from app.worker.models import Job
from app.worker.paypal_events import paypal_inbox_handlers
from app.worker.runner import JobRunner
from app.worker.simulated import RoutingRefundGateway, SimulatedRefundGateway

log = logging.getLogger("doorstep.worker")

SWEEP_EVERY_S = 300


class ReceiptingRefundHandler:
    """Runs the policy's refund handler, then signs a receipt for a completed refund (PR-01)."""

    def __init__(self, inner: RefundJobHandler, signer: ReceiptSigner, clock: Any) -> None:
        self.inner = inner
        self.signer = signer
        self.clock = clock

    def run(self, session: Session, job: Job) -> None:
        self.inner.run(session, job)
        ma = session.get(MoneyAction, job.payload.get("money_action_id"))
        if ma is None or ma.case_id is None or ma.status != MoneyActionStatus.COMPLETED.value:
            return
        from app.proof.models import Receipt

        already = session.scalars(
            select(Receipt).where(Receipt.case_id == ma.case_id, Receipt.kind == "refund")
        ).first()
        if already is None:
            issue_receipt(
                session,
                self.signer,
                case_id=ma.case_id,
                kind="refund",
                payload={
                    "money_action_id": ma.id,
                    "decision_id": ma.decision_id,
                    "amount_minor": ma.amount_minor,
                    "currency": ma.currency,
                    "paypal_refund_id": ma.paypal_refund_id,
                    "paypal_status": ma.paypal_status,
                },
                at=self.clock.now(),
            )

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        self.inner.on_failed(session, job, error)


def build_worker(rt: Runtime) -> tuple[InboxProcessor, JobRunner]:
    rulebook = load_rules(rt.deps.rules_dir)
    handlers: dict[str, Any] = {}
    sim = SimulatedRefundGateway(rt.session_factory)
    if rt.paypal is not None:
        from app.policy.gateway import RestRefundGateway

        real: Any = RestRefundGateway(rt.paypal)
        handlers[CAPTURE_ORDER] = CaptureOrderHandler(rt.services.orders, rt.clock)
        handlers[ADD_TRACKING] = AddTrackingHandler(rt.services.orders, rt.clock)
    else:
        real = NotConfigured("PayPal refunds", "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET")
    refunds = RefundJobHandler(RoutingRefundGateway(real, sim), rulebook, rt.clock)
    handlers[REFUND_JOB] = ReceiptingRefundHandler(refunds, rt.services.receipts, rt.clock)
    if rt.shippo is not None:
        handlers[REGISTER_TRACK] = RegisterTrackHandler(rt.shippo)
    inbox_handlers: dict[tuple[str, str], Any] = {
        ("shippo", "track_updated"): handle_shippo_track_updated,
        **paypal_inbox_handlers(rulebook, rt.clock),
    }
    inbox = InboxProcessor(rt.session_factory, inbox_handlers, rt.clock, rt.settings)
    runner = JobRunner(rt.session_factory, handlers, rt.clock, rt.settings)
    return inbox, runner


def sweep(rt: Runtime) -> int:
    with rt.session_factory() as s:
        flagged = ShippingService(s, rt.clock, rt.settings).sweep_stale()
        s.commit()
        return len(flagged)


def main(poll_s: float = 2.0) -> None:  # pragma: no cover - process loop
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    rt = build_runtime()
    inbox, runner = build_worker(rt)
    stop = False

    def _stop(*_: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    last_sweep = 0.0
    log.info("worker started")
    while not stop:
        try:
            done = inbox.run_pending() + runner.run_due()
            if time.monotonic() - last_sweep > SWEEP_EVERY_S:
                last_sweep = time.monotonic()
                if (n := sweep(rt)) > 0:
                    log.info("stale sweep flagged %d shipments", n)
        except Exception:
            log.exception("worker loop error; continuing")
            done = 0
        if not done:
            time.sleep(poll_s)
    log.info("worker stopped")


if __name__ == "__main__":  # pragma: no cover
    main()
