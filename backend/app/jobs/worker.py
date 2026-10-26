"""The worker process: runs queued jobs and a few periodic tasks.

    python -m app.jobs.worker

Periodic tasks (sending tick, reply polling, follow-up planning, retention) are registered with
`periodic()` by the modules that own them; a failure in one never stops the loop.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import time

from app.core.logging_setup import setup_logging
from app.db import session_scope
from app.jobs import queue, register_all_handlers
from app.jobs.periodic import load_periodic_modules, run_periodic_once
from app.jobs.runner import run_job

log = logging.getLogger("worker")
ALIVE_FILE = "/tmp/worker.alive"  # touched every loop; the container healthcheck reads its age

class Worker:
    def __init__(self) -> None:
        self.id = f"{socket.gethostname()}-{os.getpid()}"
        self.stop = False

    def loop(self, poll_seconds: float = 2.0) -> None:
        register_all_handlers()
        load_periodic_modules()
        log.info("worker %s started", self.id)
        # Recover anything a previous worker left half-way before taking new work.
        with session_scope() as db:
            queue.reap_stale(db, stale_seconds=0 if os.environ.get("WORKER_REAP_ALL_ON_START") == "1" else queue.STALE_AFTER_SECONDS)
        while not self.stop:
            try:
                with open(ALIVE_FILE, "a"):
                    os.utime(ALIVE_FILE, None)
                with session_scope() as db:
                    queue.reap_stale(db)
                run_periodic_once()
                with session_scope() as db:
                    job = queue.claim_next(db, self.id)
                    claimed = (job.id, job.type, dict(job.payload or {})) if job else None
                if claimed:
                    log.info("running job %s (%s)", claimed[0], claimed[1])
                    run_job(claimed[0], claimed[1], claimed[2], self.id)
                else:
                    time.sleep(poll_seconds)
            except Exception:
                log.exception("worker loop error")
                time.sleep(poll_seconds)
        log.info("worker %s stopped", self.id)


def main() -> None:
    setup_logging()
    w = Worker()
    signal.signal(signal.SIGTERM, lambda *_: setattr(w, "stop", True))
    signal.signal(signal.SIGINT, lambda *_: setattr(w, "stop", True))
    w.loop()


if __name__ == "__main__":
    main()
