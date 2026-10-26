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
from collections.abc import Callable

from app.core.logging_setup import setup_logging
from app.db import session_scope
from app.jobs import queue, register_all_handlers
from app.jobs.runner import run_job

log = logging.getLogger("worker")

PERIODIC: list[tuple[str, float, Callable[[], None]]] = []
_last_run: dict[str, float] = {}


def periodic(name: str, every_seconds: float):
    def deco(fn: Callable[[], None]):
        PERIODIC[:] = [p for p in PERIODIC if p[0] != name]  # idempotent: re-importing a module never duplicates a task
        PERIODIC.append((name, every_seconds, fn))
        return fn

    return deco


def run_periodic_once(force: bool = False) -> None:
    now = time.monotonic()
    for name, every, fn in PERIODIC:
        if force or now - _last_run.get(name, -1e9) >= every:
            _last_run[name] = now
            try:
                fn()
            except Exception:
                log.exception("periodic task %s failed", name)


class Worker:
    def __init__(self) -> None:
        self.id = f"{socket.gethostname()}-{os.getpid()}"
        self.stop = False

    def loop(self, poll_seconds: float = 2.0) -> None:
        register_all_handlers()
        _register_periodic()
        log.info("worker %s started", self.id)
        # Recover anything a previous worker left half-way before taking new work.
        with session_scope() as db:
            queue.reap_stale(db, stale_seconds=0 if os.environ.get("WORKER_REAP_ALL_ON_START") == "1" else queue.STALE_AFTER_SECONDS)
        while not self.stop:
            try:
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


def _register_periodic() -> None:
    # imported here so the modules can use @periodic without import cycles
    for mod in ("app.services.sending.engine", "app.services.replies.jobs", "app.services.pipeline_service"):
        try:
            __import__(mod)
        except ModuleNotFoundError as e:
            if e.name is None or not (mod == e.name or mod.startswith(e.name + ".")):
                raise


def main() -> None:
    setup_logging()
    w = Worker()
    signal.signal(signal.SIGTERM, lambda *_: setattr(w, "stop", True))
    signal.signal(signal.SIGINT, lambda *_: setattr(w, "stop", True))
    w.loop()


if __name__ == "__main__":
    main()
