"""Worker process: `python -m app.worker.main`. Processes the webhook inbox, runs due jobs
(PayPal captures, tracking, refunds; Shippo track registration) and sweeps stale tracking.

Each loop is small and idempotent, so a crash or a second worker is harmless (row locks with
SKIP LOCKED, unique dedupe keys, PayPal-Request-Id on every write).
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.ai.recovery import RecoveryDrafter
from app.cases.recovery import DRAFT_RECOVERY, DraftRecoveryHandler, sweep_expired
from app.checkout.address import AddressChecker
from app.orders.models import MoneyAction
from app.policy.evidence import DISPUTE_EVIDENCE_JOB, DisputeEvidenceJobHandler
from app.policy.offers import DISPUTE_OFFER_JOB, OfferJobHandler
from app.policy.rules import load_rules
from app.policy.service import REFUND_JOB, PolicyService, RefundJobHandler
from app.proof.receipts import ReceiptSigner, issue_refund_receipt
from app.runtime import NotConfigured, Runtime, build_runtime
from app.shipping.events import handle_shippo_track_updated
from app.shipping.service import REGISTER_TRACK, RegisterTrackHandler, ShippingService
from app.worker.approved import CompleteApprovedHandler, sweep_unreturned_approvals
from app.worker.handlers import ADD_TRACKING, CAPTURE_ORDER, AddTrackingHandler, CaptureOrderHandler
from app.worker.inbox import InboxProcessor
from app.worker.kinds import COMPLETE_APPROVED
from app.worker.models import Job
from app.worker.paypal_events import paypal_inbox_handlers
from app.worker.runner import JobRunner
from app.worker.simulated import RoutingRefundGateway, SimulatedRefundGateway

log = logging.getLogger("doorstep.worker")

SWEEP_EVERY_S = 300

# Jobs that call a model and can take tens of seconds. They run in a lane of their own (WK-01).
MODEL_JOBS = frozenset({DRAFT_RECOVERY})


class ReceiptingRefundHandler:
    """Runs the policy's refund handler, then signs a receipt for a completed refund (PR-01)."""

    def __init__(self, inner: RefundJobHandler, signer: ReceiptSigner, clock: Any) -> None:
        self.inner = inner
        self.signer = signer
        self.clock = clock

    def run(self, session: Session, job: Job) -> None:
        self.inner.run(session, job)
        ma = session.get(MoneyAction, job.payload.get("money_action_id"))
        if ma is not None:
            issue_refund_receipt(session, self.signer, ma, at=self.clock.now())

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        self.inner.on_failed(session, job, error)


def refund_gateway(rt: Runtime) -> RoutingRefundGateway:
    """PayPal for real orders, the simulator for demo orders."""
    sim = SimulatedRefundGateway(rt.session_factory)
    if rt.paypal is not None:
        from app.policy.gateway import RestRefundGateway

        real: Any = RestRefundGateway(rt.paypal)
    else:
        real = NotConfigured("PayPal refunds", "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET")
    return RoutingRefundGateway(real, sim)


def build_worker(rt: Runtime) -> tuple[InboxProcessor, JobRunner]:
    rulebook = load_rules(rt.deps.rules_dir)
    handlers: dict[str, Any] = {}
    if rt.paypal is not None:
        handlers[CAPTURE_ORDER] = CaptureOrderHandler(rt.services.orders, rt.clock)
        handlers[ADD_TRACKING] = AddTrackingHandler(rt.services.orders, rt.clock)
        handlers[COMPLETE_APPROVED] = CompleteApprovedHandler(  # CK-08
            rt.services.orders,
            rt.services.approved,
            AddressChecker(rt.services.address_validator),
            rulebook,
            rt.clock,
            rt.settings,
        )
    gateway = refund_gateway(rt)
    refunds = RefundJobHandler(gateway, rulebook, rt.clock, disputes=gateway)  # live dispute read
    handlers[DISPUTE_EVIDENCE_JOB] = DisputeEvidenceJobHandler(  # DE-01..04
        gateway, rt.clock, rt.services.receipts
    )
    handlers[DISPUTE_OFFER_JOB] = OfferJobHandler(gateway, rt.clock, rt.services.receipts)  # PP-09
    handlers[REFUND_JOB] = ReceiptingRefundHandler(refunds, rt.services.receipts, rt.clock)
    if rt.shippo is not None:
        handlers[REGISTER_TRACK] = RegisterTrackHandler(rt.shippo)
    llm = rt.services.explainer.llm if rt.services.explainer is not None else None
    handlers[DRAFT_RECOVERY] = DraftRecoveryHandler(RecoveryDrafter(llm))  # RC-03
    inbox_handlers: dict[tuple[str, str], Any] = {
        ("shippo", "track_updated"): handle_shippo_track_updated,
        **paypal_inbox_handlers(rulebook, rt.clock, rt.services.receipts),
    }
    inbox = InboxProcessor(rt.session_factory, inbox_handlers, rt.clock, rt.settings)
    runner = JobRunner(rt.session_factory, handlers, rt.clock, rt.settings)
    return inbox, runner


def split_lanes(runner: JobRunner) -> tuple[JobRunner, JobRunner]:
    """WK-01: (everything else, model jobs). A slow model draft never holds up a refund."""
    rest = {k: h for k, h in runner.handlers.items() if k not in MODEL_JOBS}
    models = {k: h for k, h in runner.handlers.items() if k in MODEL_JOBS}
    lane = lambda hs: JobRunner(runner.session_factory, hs, runner.clock, runner.settings)  # noqa: E731
    return lane(rest), lane(models)


def _model_lane(
    runner: JobRunner, should_stop: Callable[[], bool], poll_s: float
) -> None:  # pragma: no cover - thread loop
    while not should_stop():
        try:
            done = runner.run_due()
        except Exception:
            log.exception("model job lane error; continuing")
            done = 0
        if not done:
            time.sleep(poll_s)


def sweep(rt: Runtime) -> int:
    with rt.session_factory() as s:
        flagged = ShippingService(s, rt.clock, rt.settings).sweep_stale()
        expired = sweep_expired(s, rt.clock.now())  # RC-06: carrier claim deadlines
        s.commit()
        if expired:
            log.info("sweep expired %d carrier claim(s) past their deadline", len(expired))
    try:
        sweep_refunds(rt)
    except Exception:  # a PayPal read failing must not stop the other sweeps; next sweep retries
        log.exception("pending refund sweep failed; retrying next sweep")
    try:
        sweep_approvals(rt)
    except Exception:  # same: the other sweeps already ran; next sweep retries
        log.exception("approved checkout sweep failed; retrying next sweep")
    return len(flagged)


def sweep_approvals(rt: Runtime) -> int:
    """CK-08: queue a PayPal check for checkouts whose buyer never came back from PayPal."""
    if rt.paypal is None:  # nothing can capture without PayPal
        return 0
    with rt.session_factory() as s:
        queued = sweep_unreturned_approvals(s, rt.clock)
        s.commit()
    if queued:
        log.info("approved checkout sweep: asking PayPal about %d order(s)", queued)
    return queued


def sweep_refunds(rt: Runtime) -> list[str]:
    """PO-13: confirm pending refunds from PayPal in case the webhook never comes."""
    with rt.session_factory() as s:
        policy = PolicyService(s, load_rules(rt.deps.rules_dir), rt.clock)
        changed = policy.sweep_pending_refunds(refund_gateway(rt))
        for ma_id in changed:
            ma = s.get_one(MoneyAction, ma_id)
            issue_refund_receipt(s, rt.services.receipts, ma, at=rt.clock.now())
        s.commit()
    if changed:
        log.info("refund sweep: PayPal answered %d pending refund(s)", len(changed))
    return changed


def run_loop(rt: Runtime, should_stop: Callable[[], bool], poll_s: float = 2.0) -> None:
    """The worker loop. Used by the worker process and, on a one-service deploy, by a thread
    inside the web process (EMBEDDED_WORKER=1). Every step is idempotent, so both are safe."""
    inbox, runner = build_worker(rt)
    runner, model_runner = split_lanes(runner)
    threading.Thread(
        target=_model_lane,
        args=(model_runner, should_stop, poll_s),
        name="doorstep-model-jobs",
        daemon=True,
    ).start()
    last_sweep = 0.0
    log.info("worker started")
    while not should_stop():
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


def main(poll_s: float = 2.0) -> None:  # pragma: no cover - process loop
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = False

    def _stop(*_: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run_loop(build_runtime(), lambda: stop, poll_s)


if __name__ == "__main__":  # pragma: no cover
    main()
