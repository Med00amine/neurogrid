"""Registry of periodic worker tasks.

Kept in its own module on purpose: the worker is started with `python -m app.jobs.worker`, which makes Python load
that file as `__main__`. A registry living in the worker module would therefore exist twice (once as `__main__`,
once as `app.jobs.worker` when other modules import it) and the tasks would silently never run.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

log = logging.getLogger("worker")

PERIODIC: list[tuple[str, float, Callable[[], None]]] = []
_last_run: dict[str, float] = {}


def periodic(name: str, every_seconds: float):
    def deco(fn: Callable[[], None]):
        PERIODIC[:] = [p for p in PERIODIC if p[0] != name]  # idempotent: re-importing a module never duplicates a task
        PERIODIC.append((name, every_seconds, fn))
        return fn

    return deco


def run_periodic_once(force: bool = False) -> list[str]:
    """Run every task that is due. A failure in one never stops the others. Returns the names that ran."""
    now = time.monotonic()
    ran = []
    for name, every, fn in list(PERIODIC):
        if force or now - _last_run.get(name, -1e9) >= every:
            _last_run[name] = now
            ran.append(name)
            try:
                fn()
            except Exception:
                log.exception("periodic task %s failed", name)
    return ran


def load_periodic_modules() -> None:
    """Import every module that registers a periodic task."""
    for mod in ("app.services.sending.engine", "app.services.replies.jobs", "app.services.pipeline_service", "app.services.retention"):
        __import__(mod)
