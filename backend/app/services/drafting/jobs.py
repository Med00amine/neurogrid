"""Background job: generate drafts for a batch of contacts (never approves or sends anything)."""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import func, select

from app.db import session_scope
from app.jobs import queue
from app.jobs.runner import JobContext, JobFailure, job_handler
from app.models import Contact, EmailTemplate, UsageRecord
from app.services import llm as llm_mod
from app.services.drafting import generator, service

log = logging.getLogger("drafting.job")


@job_handler("generate_drafts")
def handle(ctx: JobContext) -> dict:
    ids = [int(x) for x in ctx.payload["contact_ids"]]
    template_id = int(ctx.payload["template_id"])
    step = int(ctx.payload.get("step", 1))
    tone, length = ctx.payload.get("tone"), ctx.payload.get("length")
    use_llm = bool(ctx.payload.get("use_llm", True))
    keys = [f"d{i}" for i in ids]
    with session_scope() as db:
        queue.ensure_items(db, ctx.job_id, keys)
        queue.set_progress(db, ctx.job_id, total=len(keys))
        todo = queue.pending_item_keys(db, ctx.job_id)
        counts = queue.item_counts(db, ctx.job_id)
        if db.get(EmailTemplate, template_id) is None:
            raise JobFailure("Template not found", "bad_input")
    done, failed = counts.get("done", 0) + counts.get("skipped", 0), counts.get("failed", 0)
    created = skipped = 0
    stop: tuple[str, str] | None = None
    for key in todo:
        if ctx.should_cancel():
            break
        cid = int(key[1:])
        try:
            with session_scope() as db:
                contact, template = db.get(Contact, cid), db.get(EmailTemplate, template_id)
                if contact is None:
                    queue.mark_item(db, ctx.job_id, key, "failed", error="contact not found")
                    failed += 1
                    continue
                if not generator.usable_findings(db, contact.company_id):
                    queue.mark_item(db, ctx.job_id, key, "skipped", error="No approved research findings: review the research first")
                    skipped += 1
                    done += 1
                    continue
                spent = Decimal(db.execute(select(func.coalesce(func.sum(UsageRecord.cost_usd), 0)).where(UsageRecord.job_id == ctx.job_id)).scalar_one())
                try:
                    d = service.create_draft(db, contact, template, ctx.payload.get("actor") or "job", step=step, tone=tone, length=length, use_llm=use_llm, job_id=ctx.job_id)
                except service.DraftError as e:
                    queue.mark_item(db, ctx.job_id, key, "skipped", error=str(e))
                    skipped += 1
                    done += 1
                    continue
                queue.mark_item(db, ctx.job_id, key, "done", result={"draft_id": d.id})
                created += 1
                done += 1
        except llm_mod.LLMError as e:
            with session_scope() as db:
                queue.mark_item(db, ctx.job_id, key, "failed", error=str(e))
            failed += 1
            if e.category in ("auth", "budget_exceeded", "rate_limited"):
                stop = (str(e), e.category)
                break
        except Exception as e:  # noqa: BLE001
            log.exception("draft generation failed for contact %s", cid)
            with session_scope() as db:
                queue.mark_item(db, ctx.job_id, key, "failed", error=f"{type(e).__name__}: {e}"[:500])
            failed += 1
        ctx.progress(done=done, failed=failed)
    ctx.progress(done=done, failed=failed)
    result = {"created": created, "skipped": skipped, "failed": failed}
    if stop:
        if stop[1] == "auth":
            raise JobFailure(stop[0], "auth")
        ctx.mark_partial(stop[0], stop[1])
    elif failed:
        ctx.mark_partial(f"{failed} draft(s) could not be generated (see the items list)", "unexpected")
    return result
