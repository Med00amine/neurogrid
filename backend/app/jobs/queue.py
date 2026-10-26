"""Persistent job queue on PostgreSQL.

* `enqueue` with an idempotency key is a no-op if that key already exists (safe to call repeatedly).
* `claim_next` uses SELECT … FOR UPDATE SKIP LOCKED so several workers never take the same job.
* Workers heartbeat; `reap_stale` returns jobs of dead workers to `pending` so they resume
  (handlers are written to be resumable/idempotent via `job_items` and checkpoints).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.events import audit
from app.models import Job, JobItem

STALE_AFTER_SECONDS = 90

SUGGESTED_ACTIONS = {
    "network": "Check the server's internet access and the target site, then retry. Completed items are not repeated.",
    "budget_exceeded": "The monthly or per-job budget was reached. Raise it in Settings → Budgets, or wait for next month, then retry.",
    "config_missing": "A required key or setting is missing. Add it to .env (see Settings → Credentials), restart, then retry.",
    "auth": "The provider rejected the credentials. Check the key/password in .env, then retry.",
    "rate_limited": "The provider is rate limiting. Wait a few minutes and retry; reduce the batch size if it repeats.",
    "worker_lost": "The worker stopped while running this job. Retry: it resumes where it stopped and never repeats finished items.",
    "bad_input": "The input was not valid. Fix the file/mapping or the selection and start a new job.",
    "unexpected": "Unexpected error. Open the logs (docker compose logs worker) for details; retry once, then report it.",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    db: Session,
    type: str,
    payload: dict[str, Any] | None = None,
    *,
    title: str | None = None,
    idempotency_key: str | None = None,
    created_by: str | None = None,
    priority: int = 100,
    max_retries: int = 3,
    total: int = 0,
    run_after: datetime | None = None,
) -> Job:
    values = dict(
        type=type, title=title, payload=payload or {}, idempotency_key=idempotency_key, created_by=created_by,
        priority=priority, max_retries=max_retries, progress_total=total, status="pending",
    )
    if run_after:
        values["run_after"] = run_after
    if idempotency_key:
        stmt = pg_insert(Job).values(**values).on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(Job.id)
        new_id = db.execute(stmt).scalar_one_or_none()
        if new_id is None:
            return db.execute(select(Job).where(Job.idempotency_key == idempotency_key)).scalar_one()
        db.flush()
        job = db.get(Job, new_id)
        db.refresh(job)
        return job
    job = Job(**values)
    db.add(job)
    db.flush()
    return job


def claim_next(db: Session, worker_id: str, types: list[str] | None = None) -> Job | None:
    stmt = (
        select(Job)
        .where(Job.status == "pending", Job.run_after <= func.now())
        .order_by(Job.priority, Job.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if types:
        stmt = stmt.where(Job.type.in_(types))
    job = db.execute(stmt).scalar_one_or_none()
    if job is None:
        return None
    if job.cancel_requested:
        job.status = "cancelled"
        job.finished_at = _now()
        db.flush()
        return None
    now = _now()
    job.status = "running"
    job.locked_by = worker_id
    job.locked_at = now
    job.heartbeat_at = now
    job.started_at = job.started_at or now
    job.finished_at = None
    db.flush()
    return job


def heartbeat(db: Session, job_id: UUID) -> bool:
    """Returns the cancel_requested flag so long-running handlers can stop early."""
    res = db.execute(
        update(Job).where(Job.id == job_id, Job.status == "running").values(heartbeat_at=_now()).returning(Job.cancel_requested)
    ).scalar_one_or_none()
    return bool(res)


def reap_stale(db: Session, stale_seconds: int = STALE_AFTER_SECONDS) -> int:
    """Jobs whose worker vanished go back to `pending` (or `failed` after too many restarts)."""
    cutoff = _now() - timedelta(seconds=stale_seconds)
    rows = db.execute(
        select(Job).where(Job.status == "running", func.coalesce(Job.heartbeat_at, Job.locked_at) < cutoff).with_for_update(skip_locked=True)
    ).scalars().all()
    for job in rows:
        job.locked_by = None
        job.retry_count += 1
        if job.retry_count > job.max_retries:
            job.status = "failed"
            job.finished_at = _now()
            job.error_category = "worker_lost"
            job.error_message = "The worker stopped repeatedly while running this job."
            job.suggested_action = SUGGESTED_ACTIONS["worker_lost"]
        else:
            job.status = "pending"
            job.error_category = "worker_lost"
            job.error_message = "Worker stopped; job was resumed automatically."
    db.flush()
    return len(rows)


def set_progress(db: Session, job_id: UUID, *, total: int | None = None, done: int | None = None, failed: int | None = None) -> None:
    values: dict[str, Any] = {}
    if total is not None:
        values["progress_total"] = total
    if done is not None:
        values["progress_done"] = done
    if failed is not None:
        values["progress_failed"] = failed
    if values:
        db.execute(update(Job).where(Job.id == job_id).values(**values))


def finish(db: Session, job_id: UUID, *, status: str, result: dict | None = None, error: str | None = None,
           category: str | None = None) -> None:
    job = db.get(Job, job_id)
    if job is None:
        return
    job.status = status
    job.finished_at = _now()
    job.locked_by = None
    if result is not None:
        job.result = result
    if status in ("failed", "partial"):
        job.error_message = (error or "")[:4000] or job.error_message
        job.error_category = category or job.error_category or "unexpected"
        job.suggested_action = SUGGESTED_ACTIONS.get(job.error_category, SUGGESTED_ACTIONS["unexpected"])
    else:
        job.error_message = None if status == "completed" else job.error_message
        job.error_category = None if status == "completed" else job.error_category
        job.suggested_action = None if status == "completed" else job.suggested_action


def request_cancel(db: Session, job_id: UUID, actor: str | None = None) -> Job:
    job = db.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one()
    if job.status == "pending":
        job.status = "cancelled"
        job.finished_at = _now()
        job.cancel_requested = True
    elif job.status == "running":
        job.cancel_requested = True
    audit(db, "job.cancel", actor=actor, entity_type="job", entity_id=job.id, details={"type": job.type})
    db.flush()
    return job


def retry(db: Session, job_id: UUID, actor: str | None = None) -> Job:
    job = db.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one()
    if job.status not in ("failed", "partial", "cancelled"):
        raise ValueError(f"only failed, partial or cancelled jobs can be retried (this one is {job.status})")
    job.status = "pending"
    job.retry_count += 1
    job.cancel_requested = False
    job.error_message = None
    job.error_category = None
    job.suggested_action = None
    job.finished_at = None
    job.run_after = _now()
    audit(db, "job.retry", actor=actor, entity_type="job", entity_id=job.id, details={"type": job.type})
    db.flush()
    return job


# --- items (idempotent units of work inside a job) ----------------------------------------------------------------

def ensure_items(db: Session, job_id: UUID, keys: list[str]) -> None:
    if not keys:
        return
    rows = [{"job_id": job_id, "item_key": k, "status": "pending"} for k in keys]
    db.execute(pg_insert(JobItem).values(rows).on_conflict_do_nothing(constraint="uq_job_items_job_item"))


def pending_item_keys(db: Session, job_id: UUID) -> list[str]:
    return list(db.execute(
        select(JobItem.item_key).where(JobItem.job_id == job_id, JobItem.status.in_(("pending", "running"))).order_by(JobItem.id)
    ).scalars())


def mark_item(db: Session, job_id: UUID, key: str, status: str, error: str | None = None, result: dict | None = None) -> None:
    db.execute(
        update(JobItem).where(JobItem.job_id == job_id, JobItem.item_key == key).values(
            status=status, error=(error or None) and error[:2000], result=result or {},
            finished_at=_now() if status in ("done", "failed", "skipped") else None,
        )
    )


def item_counts(db: Session, job_id: UUID) -> dict[str, int]:
    rows = db.execute(select(JobItem.status, func.count()).where(JobItem.job_id == job_id).group_by(JobItem.status)).all()
    return {s: n for s, n in rows}
