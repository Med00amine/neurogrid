from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.jobs import queue
from app.jobs.runner import HANDLERS, JobContext, JobFailure, JobPartial, job_handler, run_job, run_one
from app.models import Job

# register throw-away handlers for these tests
processed_log: list[str] = []


@job_handler("t_ok")
def _ok(ctx: JobContext):
    ctx.set_total(3)
    ctx.progress(done=3)
    return {"hello": "world"}


@job_handler("t_fail")
def _fail(ctx: JobContext):
    raise JobFailure("budget gone", "budget_exceeded")


@job_handler("t_boom")
def _boom(ctx: JobContext):
    raise RuntimeError("kaboom")


@job_handler("t_partial")
def _partial(ctx: JobContext):
    ctx.progress(done=2, failed=1)
    raise JobPartial("1 of 3 failed", {"ok": 2}, "network")


@job_handler("t_items")
def _items(ctx: JobContext):
    keys = [f"item{i}" for i in range(6)]
    with session_scope() as db:
        queue.ensure_items(db, ctx.job_id, keys)
    for key in queue_pending(ctx):
        processed_log.append(key)
        with session_scope() as db:
            queue.mark_item(db, ctx.job_id, key, "done")
        if ctx.payload.get("crash_after") and len([k for k in processed_log if k in keys]) == ctx.payload["crash_after"]:
            ctx.payload["crash_after"] = 0
            raise SystemExit("simulated process death")
    return {"processed": len(processed_log)}


def queue_pending(ctx):
    with session_scope() as db:
        return queue.pending_item_keys(db, ctx.job_id)


@job_handler("t_cancellable")
def _cancellable(ctx: JobContext):
    for i in range(50):
        if ctx.should_cancel():
            return {"stopped_at": i}
    return {"stopped_at": None}


def test_enqueue_with_idempotency_key_is_safe_to_repeat(database_url):
    with session_scope() as db:
        a = queue.enqueue(db, "t_ok", {"n": 1}, idempotency_key="same")
        b = queue.enqueue(db, "t_ok", {"n": 2}, idempotency_key="same")
        assert a.id == b.id
        assert db.execute(select(Job)).scalars().all().__len__() == 1


def test_successful_job_lifecycle(database_url):
    with session_scope() as db:
        queue.enqueue(db, "t_ok", {})
    assert run_one("w1") is True
    assert run_one("w1") is False  # nothing left
    with session_scope() as db:
        j = db.execute(select(Job)).scalar_one()
        assert j.status == "completed" and j.result == {"hello": "world"}
        assert j.progress_total == 3 and j.progress_done == 3
        assert j.started_at and j.finished_at and j.finished_at >= j.started_at
        assert j.error_message is None


def test_failure_records_error_and_suggested_action(database_url):
    with session_scope() as db:
        queue.enqueue(db, "t_fail", {})
        queue.enqueue(db, "t_boom", {})
    run_one(); run_one()
    with session_scope() as db:
        by_type = {j.type: j for j in db.execute(select(Job)).scalars()}
        assert by_type["t_fail"].status == "failed" and by_type["t_fail"].error_category == "budget_exceeded"
        assert "budget" in by_type["t_fail"].suggested_action.lower()
        assert by_type["t_boom"].status == "failed" and "kaboom" in by_type["t_boom"].error_message


def test_partial_status(database_url):
    with session_scope() as db:
        queue.enqueue(db, "t_partial", {})
    run_one()
    with session_scope() as db:
        j = db.execute(select(Job)).scalar_one()
        assert j.status == "partial" and j.progress_done == 2 and j.progress_failed == 1
        assert j.suggested_action


def test_two_workers_never_claim_the_same_job(database_url):
    with session_scope() as db:
        for i in range(8):
            queue.enqueue(db, "t_ok", {"i": i})
    claimed: list = []
    lock = threading.Lock()

    def worker(name):
        while True:
            with session_scope() as db:
                job = queue.claim_next(db, name)
                jid = job.id if job else None
            if jid is None:
                return
            with lock:
                claimed.append(jid)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(claimed) == 8 and len(set(claimed)) == 8


def test_stale_running_job_is_resumed_then_eventually_failed(database_url):
    with session_scope() as db:
        j = queue.enqueue(db, "t_ok", {}, max_retries=1)
        jid = j.id
    with session_scope() as db:
        queue.claim_next(db, "dead-worker")
    with session_scope() as db:
        db.get(Job, jid).heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    with session_scope() as db:
        assert queue.reap_stale(db) == 1
        j = db.get(Job, jid)
        assert j.status == "pending" and j.retry_count == 1
    # worker dies again
    with session_scope() as db:
        queue.claim_next(db, "dead-worker-2")
        db.get(Job, jid).heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    with session_scope() as db:
        queue.reap_stale(db)
        j = db.get(Job, jid)
        assert j.status == "failed" and j.error_category == "worker_lost" and j.suggested_action


def test_restart_recovery_never_repeats_completed_items(database_url):
    processed_log.clear()
    with session_scope() as db:
        j = queue.enqueue(db, "t_items", {"crash_after": 3}, max_retries=2)
        jid = j.id
    with pytest.raises(SystemExit):
        run_one("worker-A")  # dies after 3 items; job remains `running`
    with session_scope() as db:
        assert db.get(Job, jid).status == "running"
        db.get(Job, jid).heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    with session_scope() as db:
        queue.reap_stale(db)
    assert run_one("worker-B")  # resumes
    assert sorted(processed_log) == [f"item{i}" for i in range(6)]  # each item exactly once
    assert len(processed_log) == 6
    with session_scope() as db:
        assert db.get(Job, jid).status == "completed"
        assert queue.item_counts(db, jid) == {"done": 6}


def test_cancel_pending_and_running(database_url):
    with session_scope() as db:
        a = queue.enqueue(db, "t_ok", {})
        queue.request_cancel(db, a.id)
        assert db.get(Job, a.id).status == "cancelled"
        b = queue.enqueue(db, "t_cancellable", {})
        bid = b.id
    with session_scope() as db:
        queue.claim_next(db, "w")
    with session_scope() as db:
        queue.request_cancel(db, bid)
        assert db.get(Job, bid).cancel_requested
    run_job(bid, "t_cancellable", {}, "w")
    with session_scope() as db:
        assert db.get(Job, bid).status == "cancelled"


def test_retry_only_failed_partial_cancelled(database_url):
    with session_scope() as db:
        j = queue.enqueue(db, "t_ok", {})
        with pytest.raises(ValueError):
            queue.retry(db, j.id)  # pending
    with session_scope() as db:
        f = queue.enqueue(db, "t_fail", {})
        fid = f.id
    run_one(); run_one()
    with session_scope() as db:
        r = queue.retry(db, fid)
        assert r.status == "pending" and r.retry_count == 1 and r.error_message is None


def test_unknown_job_type_fails_cleanly(database_url):
    with session_scope() as db:
        queue.enqueue(db, "does_not_exist", {})
    run_one()
    with session_scope() as db:
        j = db.execute(select(Job)).scalar_one()
        assert j.status == "failed" and "No handler" in j.error_message
