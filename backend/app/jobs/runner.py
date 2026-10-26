"""Job handler registry and the code that runs one claimed job."""

from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.db import session_scope
from app.jobs import queue
from app.models import Job

log = logging.getLogger("jobs")

HANDLERS: dict[str, Callable[["JobContext"], dict | None]] = {}


def job_handler(job_type: str):
    def deco(fn):
        HANDLERS[job_type] = fn
        return fn

    return deco


class JobFailure(Exception):
    """Raise from a handler to fail the job with a category (see queue.SUGGESTED_ACTIONS)."""

    def __init__(self, message: str, category: str = "unexpected"):
        super().__init__(message)
        self.category = category


class JobPartial(Exception):
    """Raise (or return via ctx.mark_partial) when some items worked and some did not."""

    def __init__(self, message: str, result: dict | None = None, category: str = "unexpected"):
        super().__init__(message)
        self.result = result or {}
        self.category = category


class JobContext:
    def __init__(self, job_id: UUID, job_type: str, payload: dict[str, Any], worker_id: str):
        self.job_id = job_id
        self.type = job_type
        self.payload = payload
        self.worker_id = worker_id
        self.done = 0
        self.failed = 0
        self._cancel = False
        self.partial_message: str | None = None
        self.partial_category = "unexpected"

    # progress / cancellation ------------------------------------------------------------------------------
    def set_total(self, n: int) -> None:
        with session_scope() as db:
            queue.set_progress(db, self.job_id, total=n)

    def progress(self, done: int | None = None, failed: int | None = None, total: int | None = None) -> None:
        if done is not None:
            self.done = done
        if failed is not None:
            self.failed = failed
        with session_scope() as db:
            queue.set_progress(db, self.job_id, done=self.done, failed=self.failed, total=total)

    def should_cancel(self) -> bool:
        with session_scope() as db:
            return bool(db.execute(select(Job.cancel_requested).where(Job.id == self.job_id)).scalar_one_or_none())

    def mark_partial(self, message: str, category: str = "unexpected") -> None:
        self.partial_message = message
        self.partial_category = category


def run_job(job_id: UUID, job_type: str, payload: dict[str, Any], worker_id: str) -> None:
    """Execute a claimed job, keep its heartbeat alive, and record the outcome."""
    handler = HANDLERS.get(job_type)
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(10):
            try:
                with session_scope() as db:
                    queue.heartbeat(db, job_id)
            except Exception:  # pragma: no cover - heartbeat must never kill the job
                log.exception("heartbeat failed")

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    ctx = JobContext(job_id, job_type, payload, worker_id)
    try:
        if handler is None:
            raise JobFailure(f"No handler registered for job type '{job_type}'", "unexpected")
        result = handler(ctx) or {}
        cancelled = ctx.should_cancel()
        with session_scope() as db:
            if cancelled:
                queue.finish(db, job_id, status="cancelled", result=result)
            elif ctx.partial_message:
                queue.finish(db, job_id, status="partial", result=result, error=ctx.partial_message, category=ctx.partial_category)
            else:
                queue.finish(db, job_id, status="completed", result=result)
    except JobPartial as e:
        with session_scope() as db:
            queue.finish(db, job_id, status="partial", result=e.result, error=str(e), category=e.category)
    except JobFailure as e:
        with session_scope() as db:
            queue.finish(db, job_id, status="failed", error=str(e), category=e.category)
    except Exception as e:
        log.error("job %s (%s) crashed: %s", job_id, job_type, e)
        log.error(traceback.format_exc())
        with session_scope() as db:
            queue.finish(db, job_id, status="failed", error=f"{type(e).__name__}: {e}", category="unexpected")
    finally:
        stop.set()
        # keep final counters consistent with what the context saw
        try:
            with session_scope() as db:
                queue.set_progress(db, job_id, done=ctx.done or None, failed=ctx.failed or None)
        except Exception:  # pragma: no cover
            pass


def run_one(worker_id: str = "inline") -> bool:
    """Claim and run a single pending job in the current process. Returns False if nothing was pending."""
    with session_scope() as db:
        job = queue.claim_next(db, worker_id)
        if job is None:
            return False
        jid, jtype, payload = job.id, job.type, dict(job.payload or {})
    run_job(jid, jtype, payload, worker_id)
    return True
