"""Background job: research a batch of companies. Resumable: finished companies are never repeated."""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import func, select

from app.db import session_scope
from app.jobs import queue
from app.jobs.runner import JobContext, JobFailure, job_handler
from app.models import UsageRecord
from app.services import llm as llm_mod
from app.services.research.pipeline import ResearchCancelled, research_company

log = logging.getLogger("research.job")


@job_handler("research_companies")
def handle(ctx: JobContext) -> dict:
    company_ids: list[int] = [int(x) for x in ctx.payload.get("company_ids", [])]
    force = bool(ctx.payload.get("force", False))
    keys = [f"c{cid}" for cid in company_ids]
    with session_scope() as db:
        queue.ensure_items(db, ctx.job_id, keys)
        queue.set_progress(db, ctx.job_id, total=len(keys))
        todo = queue.pending_item_keys(db, ctx.job_id)
        counts = queue.item_counts(db, ctx.job_id)
    done, failed = counts.get("done", 0), counts.get("failed", 0)
    stats = {"researched": 0, "cached": 0, "no_pages": 0, "findings": 0}
    stop_reason: tuple[str, str] | None = None

    for key in todo:
        if ctx.should_cancel():
            break
        cid = int(key[1:])
        with session_scope() as db:
            spent = Decimal(db.execute(select(func.coalesce(func.sum(UsageRecord.cost_usd), 0)).where(UsageRecord.job_id == ctx.job_id)).scalar_one())
        try:
            with session_scope() as db:
                outcome = research_company(db, cid, force=force, job_id=ctx.job_id, job_spent=spent, should_cancel=ctx.should_cancel)
                queue.mark_item(db, ctx.job_id, key, "done" if outcome.status != "failed" else "failed",
                                error=None if outcome.status != "failed" else "; ".join(outcome.notes)[:1000],
                                result={"run_id": outcome.run_id, "findings": outcome.findings, "pages": outcome.pages, "cached": outcome.cached})
            if outcome.status == "failed":
                failed += 1
                stats["no_pages"] += 1
            else:
                done += 1
                stats["cached" if outcome.cached else "researched"] += 1
                stats["findings"] += outcome.findings
        except ResearchCancelled:
            break
        except llm_mod.LLMError as e:
            with session_scope() as db:
                queue.mark_item(db, ctx.job_id, key, "failed", error=str(e))
            failed += 1
            if e.category in ("auth", "budget_exceeded", "rate_limited"):
                stop_reason = (str(e), e.category)
                break
        except Exception as e:  # noqa: BLE001 - one bad company must not stop the batch
            log.exception("research failed for company %s", cid)
            with session_scope() as db:
                queue.mark_item(db, ctx.job_id, key, "failed", error=f"{type(e).__name__}: {e}"[:1000])
            failed += 1
        ctx.progress(done=done, failed=failed)

    ctx.progress(done=done, failed=failed)
    result = {**stats, "done": done, "failed": failed}
    if stop_reason:
        if stop_reason[1] == "auth":
            raise JobFailure(stop_reason[0], "auth")
        ctx.mark_partial(stop_reason[0], stop_reason[1])
    elif failed and done:
        ctx.mark_partial(f"{failed} of {len(keys)} companies could not be researched (see the items list)", "network")
    elif failed and not done:
        raise JobFailure(f"None of the {failed} companies could be researched (no readable website?)", "network")
    return result
