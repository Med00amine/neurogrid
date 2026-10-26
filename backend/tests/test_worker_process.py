"""The worker must work when started exactly like production: `python -m app.jobs.worker`.

Regression test: the periodic-task registry used to live in the worker module itself. Started with `-m`, that module is
`__main__`, so other modules registered their tasks in a second copy and the real worker silently never sent anything.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.settings_store import set_group
from app.db import session_scope
from app.jobs import queue
from app.models import Campaign, Job, Message
from app.services import jurisdictions
from app.services.sending import campaigns as camp
from app.services.seed import seed_defaults
from tests.test_sending import SENDER, confirm, new_campaign, ready_contacts

BACKEND = Path(__file__).resolve().parents[1]


def wait_for(predicate, timeout=40.0, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(step)
    return False


@pytest.fixture()
def worker_process(database_url):
    env = {**os.environ, "DATABASE_URL": database_url, "ENVIRONMENT": "test", "LOG_JSON": "false", "LOG_LEVEL": "WARNING", "PYTHONPATH": str(BACKEND)}
    for k in ("SMTP_HOST", "IMAP_HOST", "LIVE_SENDING_ENABLED"):
        env.pop(k, None)
    proc = subprocess.Popen([sys.executable, "-m", "app.jobs.worker"], cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    yield proc
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_periodic_tasks_are_registered_in_the_shared_registry():
    from app.jobs.periodic import PERIODIC, load_periodic_modules

    load_periodic_modules()
    assert {"send_tick", "poll_replies_scheduler", "followup_guard", "retention_purge"} <= {n for n, _, _ in PERIODIC}


def test_production_style_worker_sends_a_queued_dry_run_and_runs_jobs(database_url, worker_process):
    with session_scope() as db:
        seed_defaults(db)
        set_group(db, "sender", SENDER)
        set_group(db, "general", {"timezone": "UTC"})
        jurisdictions.attest(db, "FR", actor="t", note="assessed for the test run")
    ids = ready_contacts(2)
    cid, n = new_campaign(ids, mode="dry_run")
    confirm(cid, n)
    with session_scope() as db:
        job_id = queue.enqueue(db, "does_not_exist_but_is_claimed", {}).id

    def done():
        with session_scope() as db:
            statuses = set(db.execute(select(Message.status)).scalars())
            return statuses == {"submitted"} and db.get(Campaign, cid).status == "completed"

    assert wait_for(done), "the worker process never sent the queued dry-run messages (periodic tasks not running?)"
    assert wait_for(lambda: _job_status(job_id) == "failed")  # the worker also claims and runs queued jobs
    assert worker_process.poll() is None  # and is still alive


def _job_status(job_id):
    with session_scope() as db:
        return db.get(Job, job_id).status
