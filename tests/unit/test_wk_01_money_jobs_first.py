"""WK-01: money never waits behind AI work in the job queue.

Found on Nov 9: an approved refund sat queued for 30 s while the worker drafted claim packs with a
model, one after another, so the seller's "refund on its way" never landed during the demo.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.worker.models import Job
from app.worker.runner import JobRunner, enqueue

REFUND = "paypal.refund"
DRAFT = "draft_recovery"


class Recorder:
    """A handler that records the order jobs ran in; `then` runs inside a job, as if the seller
    approved a refund while a model call was in flight."""

    def __init__(self, ran: list[str], then=None) -> None:
        self.ran, self.then = ran, then

    def run(self, session: Session, job: Job) -> None:
        self.ran.append(job.payload["name"])
        if self.then is not None:
            then, self.then = self.then, None
            then()

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:  # pragma: no cover
        pass


def _add(session_factory, clock, kind: str, name: str) -> None:
    with session_factory() as s:
        enqueue(s, kind, {"name": name}, clock=clock)
        s.commit()


def test_WK_01_a_refund_runs_before_older_ai_jobs(session_factory, clock, settings):
    ran: list[str] = []
    _add(session_factory, clock, DRAFT, "draft-1")
    _add(session_factory, clock, DRAFT, "draft-2")
    _add(session_factory, clock, REFUND, "refund")
    handlers = {DRAFT: Recorder(ran), REFUND: Recorder(ran)}
    assert JobRunner(session_factory, handlers, clock, settings).run_due() == 3
    assert ran == ["refund", "draft-1", "draft-2"]


def test_WK_01_a_refund_approved_during_a_model_call_goes_next(session_factory, clock, settings):
    ran: list[str] = []
    _add(session_factory, clock, DRAFT, "draft-1")
    _add(session_factory, clock, DRAFT, "draft-2")
    approve = lambda: _add(session_factory, clock, REFUND, "refund")  # noqa: E731
    handlers = {DRAFT: Recorder(ran, then=approve), REFUND: Recorder(ran)}
    assert JobRunner(session_factory, handlers, clock, settings).run_due() == 3
    assert ran == ["draft-1", "refund", "draft-2"]


def test_WK_01_each_job_runs_at_most_once_per_pass(session_factory, clock, settings):
    ran: list[str] = []
    _add(session_factory, clock, DRAFT, "draft-1")

    class Requeue(Recorder):
        def run(self, session: Session, job: Job) -> None:
            super().run(session, job)
            raise RuntimeError("model timed out")  # retried later with backoff, not in this pass

    runner = JobRunner(session_factory, {DRAFT: Requeue(ran)}, clock, settings)
    assert runner.run_due() == 1  # it ran, failed, and waits for its backoff
    assert ran == ["draft-1"]


def test_WK_01_model_jobs_run_in_their_own_lane():
    from app.worker.main import MODEL_JOBS, split_lanes

    handlers = {REFUND: Recorder([]), DRAFT: Recorder([]), "paypal.add_tracking": Recorder([])}
    runner = JobRunner(None, handlers, None, None)  # type: ignore[arg-type]
    money, models = split_lanes(runner)
    assert DRAFT in MODEL_JOBS
    assert set(money.handlers) == {REFUND, "paypal.add_tracking"}
    assert set(models.handlers) == {DRAFT}
