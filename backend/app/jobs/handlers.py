"""Job handlers. Importing this module registers them with the runner."""

from __future__ import annotations

from app.jobs.runner import JobContext, JobFailure, job_handler
from app.services import importing


@job_handler("import_batch")
def handle_import_batch(ctx: JobContext) -> dict:
    batch_id = int(ctx.payload["batch_id"])
    try:
        return importing.process_batch(
            batch_id,
            progress=lambda done, total: ctx.progress(done=done, total=total),
            should_cancel=ctx.should_cancel,
        )
    except importing.ImportError_ as e:
        raise JobFailure(str(e), "bad_input")


# Research, drafting, sending and reply handlers are registered by their own modules (see app/jobs/__init__.py).
