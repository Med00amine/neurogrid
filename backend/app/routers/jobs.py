from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import Auth, read_access, write_access
from app.db import get_db
from app.jobs import queue
from app.models import Job, JobItem, UsageRecord

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def ser(j: Job) -> dict:
    end = j.finished_at
    start = j.started_at
    duration = None
    if start:
        from datetime import datetime, timezone

        duration = ((end or datetime.now(timezone.utc)) - start).total_seconds()
    return {
        "id": str(j.id), "short_id": str(j.id)[:8], "type": j.type, "title": j.title, "status": j.status,
        "progress_total": j.progress_total, "progress_done": j.progress_done, "progress_failed": j.progress_failed,
        "retry_count": j.retry_count, "max_retries": j.max_retries, "created_at": j.created_at.isoformat(),
        "started_at": start.isoformat() if start else None, "finished_at": end.isoformat() if end else None,
        "duration_seconds": duration, "error_message": j.error_message, "error_category": j.error_category,
        "suggested_action": j.suggested_action, "cancel_requested": j.cancel_requested, "created_by": j.created_by,
        "result": j.result,
    }


@router.get("")
def list_jobs(status: str | None = None, type: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
              db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    stmt = select(Job)
    cstmt = select(func.count()).select_from(Job)
    if status:
        stmt, cstmt = stmt.where(Job.status == status), cstmt.where(Job.status == status)
    if type:
        stmt, cstmt = stmt.where(Job.type == type), cstmt.where(Job.type == type)
    rows = db.execute(stmt.order_by(Job.created_at.desc()).limit(page_size).offset((page - 1) * page_size)).scalars().all()
    counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    return {"items": [ser(j) for j in rows], "total": db.execute(cstmt).scalar_one(), "counts": counts}


@router.get("/{job_id}")
def get_job(job_id: UUID, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "Job not found")
    items = db.execute(select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.id).limit(300)).scalars().all()
    cost = db.execute(select(func.coalesce(func.sum(UsageRecord.cost_usd), 0)).where(UsageRecord.job_id == job_id)).scalar_one()
    return {**ser(j), "cost_usd_est": float(cost), "payload": j.payload, "items": [{"key": i.item_key, "status": i.status, "error": i.error} for i in items],
            "item_counts": queue.item_counts(db, job_id)}


@router.post("/{job_id}/cancel")
def cancel(job_id: UUID, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    if db.get(Job, job_id) is None:
        raise HTTPException(404, "Job not found")
    return ser(queue.request_cancel(db, job_id, auth.label))


@router.post("/{job_id}/retry")
def retry(job_id: UUID, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    if db.get(Job, job_id) is None:
        raise HTTPException(404, "Job not found")
    try:
        return ser(queue.retry(db, job_id, auth.label))
    except ValueError as e:
        raise HTTPException(409, str(e))
