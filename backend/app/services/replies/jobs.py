"""Reply polling: a job (so it shows in the Job Monitor) and a periodic enqueue."""

from __future__ import annotations

import time

from app.config import get_settings
from app.db import session_scope
from app.jobs import queue
from app.jobs.runner import JobContext, JobFailure, job_handler
from app.jobs.worker import periodic
from app.services import llm as llm_mod
from app.services.replies import processor
from app.services.replies.imap import get_mailbox


@job_handler("poll_replies")
def handle(ctx: JobContext) -> dict:
    client = get_mailbox()
    if client is None:
        raise JobFailure("IMAP is not configured (IMAP_HOST / IMAP_PASSWORD in .env)", "config_missing")
    env = get_settings()
    totals = {"fetched": 0, "processed": 0, "replies": 0, "bounces": 0, "auto": 0, "skipped": 0, "errors": 0}
    with session_scope() as db:
        llm_client = llm_mod.get_llm(db)
    for _ in range(20):  # at most 20 batches of 100 per run
        try:
            stats = processor.poll(client, env.imap_mailbox, session_scope, llm_client=llm_client)
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            if "AUTHENTICATIONFAILED" in msg.upper() or "LOGIN" in msg.upper():
                raise JobFailure("The mail server rejected the IMAP login", "auth")
            raise JobFailure(msg, "network")
        for k in totals:
            totals[k] += stats.get(k, 0)
        ctx.progress(done=totals["processed"], failed=totals["errors"])
        if not stats["has_more"] or ctx.should_cancel():
            break
    if totals["errors"]:
        ctx.mark_partial(f"{totals['errors']} message(s) could not be processed (see the worker log)", "unexpected")
    return totals


@periodic("poll_replies_scheduler", 120)
def _schedule() -> None:
    env = get_settings()
    if not (env.imap_host and env.imap_password):
        return
    bucket = int(time.time() // 300)  # one poll per 5 minutes, even with several workers
    with session_scope() as db:
        queue.enqueue(db, "poll_replies", {}, title="Check mailbox for replies and bounces", idempotency_key=f"poll_replies:{bucket}", priority=50, max_retries=1)
