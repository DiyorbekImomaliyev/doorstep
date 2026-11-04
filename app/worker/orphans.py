"""Orphaned jobs: refund jobs whose money action no longer exists (ADR-021).

Such a job can never send money; it is failed permanently and logged at ERROR. This query lets
the Control Room list them for a human.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orders.models import MoneyAction
from app.policy.service import REFUND_JOB
from app.worker.models import Job
from app.worker.runner import FAILED


def orphaned_refund_jobs(session: Session) -> list[Job]:
    """Failed refund jobs whose payload names a money action that does not exist."""
    jobs = session.scalars(
        select(Job).where(Job.kind == REFUND_JOB, Job.status == FAILED).order_by(Job.created_at)
    )
    return [
        job
        for job in jobs
        if session.get(MoneyAction, str(job.payload.get("money_action_id", ""))) is None
    ]
