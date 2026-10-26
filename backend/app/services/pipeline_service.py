"""Sales pipeline: stages, tasks, notes, deals, and the follow-up stop rules."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.constants import PIPELINE_STAGES
from app.core.events import audit, record_event
from app.db import session_scope
from app.jobs.worker import periodic
from app.models import Company, Contact, Deal, LeadScore, Note, Suppression, Task
from app.services.replies.processor import stop_followups

log = logging.getLogger("pipeline")

BOARD_STAGES = [s for s in PIPELINE_STAGES if s != "new"]
TERMINAL = ("won", "lost", "do_not_contact")


class PipelineError(ValueError):
    pass


def board(db: Session, per_stage: int = 30, search: str | None = None) -> dict:
    counts = dict(db.execute(select(Contact.pipeline_stage, func.count()).group_by(Contact.pipeline_stage)).all())
    cols = []
    for stage in PIPELINE_STAGES:
        if stage == "new":
            cols.append({"stage": stage, "count": counts.get(stage, 0), "items": []})  # 'new' is the raw list; see Contacts
            continue
        stmt = (select(Contact, Company, LeadScore.total).outerjoin(Company, Company.id == Contact.company_id)
                .outerjoin(LeadScore, LeadScore.contact_id == Contact.id).where(Contact.pipeline_stage == stage))
        if search:
            t = "%" + search.strip().replace("%", "") + "%"
            stmt = stmt.where(or_(Contact.email_normalized.ilike(t), Contact.name.ilike(t), Company.name.ilike(t)))
        rows = db.execute(stmt.order_by(Contact.next_action_at.asc().nulls_last(), Contact.id.desc()).limit(per_stage)).all()
        ids = [c.id for c, _, _ in rows]
        next_task = {}
        if ids:
            for cid, title, due in db.execute(select(Task.contact_id, Task.title, Task.due_at).where(Task.contact_id.in_(ids), Task.status == "open").order_by(Task.due_at.asc().nulls_last())).all():
                next_task.setdefault(cid, (title, due))
        deals = {}
        if ids:
            for cid, v, cur in db.execute(select(Deal.contact_id, func.sum(Deal.value_amount), func.max(Deal.currency)).where(Deal.contact_id.in_(ids), Deal.status != "lost").group_by(Deal.contact_id)).all():
                deals[cid] = (float(v) if v is not None else None, cur)
        cols.append({"stage": stage, "count": counts.get(stage, 0), "items": [
            {"id": c.id, "name": c.name, "email": c.email_normalized, "company": (comp.name if comp and comp.name else c.company_name_raw), "score": score,
             "next_action": next_task.get(c.id, (None, None))[0], "next_action_at": next_task[c.id][1].isoformat() if c.id in next_task and next_task[c.id][1] else None,
             "deal_value": deals.get(c.id, (None, None))[0], "currency": deals.get(c.id, (None, None))[1]} for c, comp, score in rows]})
    return {"columns": cols}


def move(db: Session, contact_id: int, stage: str, actor: str, note: str | None = None) -> Contact:
    if stage not in PIPELINE_STAGES:
        raise PipelineError("unknown stage")
    c = db.execute(select(Contact).where(Contact.id == contact_id).with_for_update()).scalar_one_or_none()
    if c is None:
        raise PipelineError("contact not found")
    if stage == "do_not_contact":
        raise PipelineError("To mark someone do-not-contact, add them to the suppression list (Contacts -> Suppress). That blocks sending everywhere.")
    if c.pipeline_stage == "do_not_contact":
        raise PipelineError("This person is on the do-not-contact list. Lift the suppression explicitly first (Settings -> Suppression list).")
    if c.pipeline_stage == stage:
        return c
    before = c.pipeline_stage
    c.pipeline_stage = stage
    record_event(db, "stage_changed", contact_id=c.id, source="user", actor=actor, data={"from": before, "to": stage, "note": note})
    if stage in ("won", "lost"):
        stop_followups(db, c.id, f"moved to {stage}")
    if stage in ("replied", "interested", "meeting_scheduled", "proposal_sent"):
        stop_followups(db, c.id, f"moved to {stage}")
    if note:
        db.add(Note(contact_id=c.id, body=f"[stage {before} -> {stage}] {note}", created_by=actor))
    return c


def add_task(db: Session, contact_id: int | None, title: str, actor: str, due_at: datetime | None = None, kind: str = "manual") -> Task:
    if contact_id is not None and db.get(Contact, contact_id) is None:
        raise PipelineError("contact not found")
    t = Task(contact_id=contact_id, title=title.strip()[:300], kind=kind, due_at=due_at, created_by=actor, status="open")
    db.add(t)
    db.flush()
    if contact_id and due_at:
        c = db.get(Contact, contact_id)
        c.next_action, c.next_action_at = t.title, due_at
    return t


def finish_task(db: Session, task_id: int, status: str, actor: str, reason: str | None = None) -> Task:
    t = db.get(Task, task_id)
    if t is None or t.status != "open":
        raise PipelineError("task not found or already closed")
    t.status = status
    t.completed_at = datetime.now(timezone.utc)
    if status == "cancelled":
        t.cancel_reason = reason or f"cancelled by {actor}"
    audit(db, f"task.{status}", actor=actor, entity_type="task", entity_id=t.id)
    return t


def summary(db: Session) -> dict:
    counts = dict(db.execute(select(Contact.pipeline_stage, func.count()).group_by(Contact.pipeline_stage)).all())
    funnel = [{"stage": s, "count": counts.get(s, 0)} for s in PIPELINE_STAGES if s not in ("new", "do_not_contact")]
    deal_rows = db.execute(select(Deal.status, Deal.currency, func.count(), func.coalesce(func.sum(Deal.value_amount), 0)).group_by(Deal.status, Deal.currency)).all()
    return {"funnel": funnel, "deals": [{"status": s, "currency": cur, "count": n, "value": float(v)} for s, cur, n, v in deal_rows]}


# --- follow-up guard ----------------------------------------------------------------------------------------------------------------


def cancel_stale_followups(db: Session) -> int:
    """Belt and braces: any open follow-up whose contact can no longer be followed up is cancelled."""
    sup_email = exists().where(and_(Suppression.active.is_(True), Suppression.kind == "email", Suppression.value == Contact.email_normalized))
    sup_dom = exists().where(and_(Suppression.active.is_(True), Suppression.kind == "domain", Suppression.value == Contact.email_domain))
    ids = db.execute(
        select(Task.id).join(Contact, Contact.id == Task.contact_id)
        .where(Task.status == "open", Task.kind == "followup",
               or_(Contact.pipeline_stage.in_(("replied", "interested", "meeting_scheduled", "proposal_sent", "won", "lost", "do_not_contact")), sup_email, sup_dom))
    ).scalars().all()
    if ids:
        db.execute(update(Task).where(Task.id.in_(ids)).values(status="cancelled", cancel_reason="stop condition detected"))
        log.info("cancelled %d stale follow-up task(s)", len(ids))
    return len(ids)


@periodic("followup_guard", 300)
def _guard() -> None:
    with session_scope() as db:
        cancel_stale_followups(db)
