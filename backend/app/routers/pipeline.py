from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import PIPELINE_STAGES
from app.core.deps import Auth, read_access, write_access
from app.core.events import audit
from app.db import get_db
from app.models import Campaign, Contact, Deal, Message, Note, Task
from app.services import pipeline_service as svc

router = APIRouter(prefix="/api", tags=["pipeline"])


@router.get("/pipeline/board")
def board(search: str | None = None, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    return svc.board(db, search=search)


@router.get("/pipeline/summary")
def summary(db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    return svc.summary(db)


class MoveBody(BaseModel):
    contact_id: int
    stage: str
    note: str | None = Field(None, max_length=1000)


@router.post("/pipeline/move")
def move(body: MoveBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        c = svc.move(db, body.contact_id, body.stage, auth.label, body.note)
    except svc.PipelineError as e:
        raise HTTPException(409, str(e))
    return {"id": c.id, "stage": c.pipeline_stage}


# --- tasks ------------------------------------------------------------------------------------------------------------------------

def _task(t: Task, c: Contact | None) -> dict:
    return {"id": t.id, "title": t.title, "kind": t.kind, "status": t.status, "due_at": t.due_at.isoformat() if t.due_at else None,
            "auto": t.auto, "cancel_reason": t.cancel_reason, "followup_step": t.followup_step, "message_id": t.source_message_id,
            "contact": {"id": c.id, "name": c.name, "email": c.email_normalized, "stage": c.pipeline_stage} if c else None,
            "overdue": bool(t.status == "open" and t.due_at and t.due_at < datetime.now(timezone.utc))}


@router.get("/tasks")
def tasks(status: str | None = "open", kind: str | None = None, contact_id: int | None = None, overdue: bool | None = None,
          page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=300), db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    stmt = select(Task, Contact).outerjoin(Contact, Contact.id == Task.contact_id)
    cstmt = select(func.count()).select_from(Task)
    conds = []
    if status:
        conds.append(Task.status == status)
    if kind:
        conds.append(Task.kind == kind)
    if contact_id:
        conds.append(Task.contact_id == contact_id)
    if overdue:
        conds.append(Task.due_at < datetime.now(timezone.utc))
    for c in conds:
        stmt, cstmt = stmt.where(c), cstmt.where(c)
    rows = db.execute(stmt.order_by(Task.due_at.asc().nulls_last(), Task.id).limit(page_size).offset((page - 1) * page_size)).all()
    return {"items": [_task(t, c) for t, c in rows], "total": db.execute(cstmt).scalar_one()}


class TaskBody(BaseModel):
    contact_id: int | None = None
    title: str = Field(min_length=2, max_length=300)
    due_at: datetime | None = None


@router.post("/tasks")
def create_task(body: TaskBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        t = svc.add_task(db, body.contact_id, body.title, auth.label, body.due_at)
    except svc.PipelineError as e:
        raise HTTPException(404, str(e))
    return _task(t, db.get(Contact, t.contact_id) if t.contact_id else None)


class TaskPatch(BaseModel):
    due_at: datetime | None = None
    title: str | None = Field(None, max_length=300)


@router.patch("/tasks/{tid}")
def patch_task(tid: int, body: TaskPatch, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    t = db.get(Task, tid)
    if t is None or t.status != "open":
        raise HTTPException(404, "Open task not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    return _task(t, db.get(Contact, t.contact_id) if t.contact_id else None)


@router.post("/tasks/{tid}/complete")
def complete_task(tid: int, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        t = svc.finish_task(db, tid, "done", auth.label)
    except svc.PipelineError as e:
        raise HTTPException(404, str(e))
    return _task(t, None)


@router.post("/tasks/{tid}/cancel")
def cancel_task(tid: int, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        t = svc.finish_task(db, tid, "cancelled", auth.label)
    except svc.PipelineError as e:
        raise HTTPException(404, str(e))
    return _task(t, None)


# --- notes + deals ----------------------------------------------------------------------------------------------------------------

class NoteBody(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


@router.post("/contacts/{cid}/notes")
def add_note(cid: int, body: NoteBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    if db.get(Contact, cid) is None:
        raise HTTPException(404, "Contact not found")
    n = Note(contact_id=cid, body=body.body.strip(), created_by=auth.label)
    db.add(n)
    db.flush()
    return {"id": n.id}


def _deal(d: Deal) -> dict:
    return {"id": d.id, "contact_id": d.contact_id, "title": d.title, "value": float(d.value_amount) if d.value_amount is not None else None,
            "currency": d.currency, "status": d.status, "source_campaign_id": d.source_campaign_id, "notes": d.notes,
            "expected_close": d.expected_close.isoformat() if d.expected_close else None}


@router.get("/deals")
def list_deals(status: str | None = None, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    stmt = select(Deal).order_by(Deal.id.desc()).limit(200)
    if status:
        stmt = stmt.where(Deal.status == status)
    return [_deal(d) for d in db.execute(stmt).scalars()]


class DealBody(BaseModel):
    contact_id: int
    title: str = Field(min_length=2, max_length=300)
    value: Decimal | None = Field(None, ge=0, le=100_000_000)
    currency: str = Field("EUR", min_length=3, max_length=3)
    source_campaign_id: int | None = None
    notes: str | None = Field(None, max_length=2000)
    expected_close: datetime | None = None


@router.post("/deals")
def create_deal(body: DealBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    c = db.get(Contact, body.contact_id)
    if c is None:
        raise HTTPException(404, "Contact not found")
    camp = body.source_campaign_id
    if camp is None:
        camp = db.execute(select(Campaign.id).join(Message, Message.campaign_id == Campaign.id).where(Message.contact_id == c.id, Message.is_dry_run.is_(False))
                          .order_by(Campaign.id.desc()).limit(1)).scalar_one_or_none()
    d = Deal(contact_id=c.id, title=body.title, value_amount=body.value, currency=body.currency.upper(), source_campaign_id=camp,
             notes=body.notes, expected_close=body.expected_close)
    db.add(d)
    db.flush()
    audit(db, "deal.create", actor=auth.label, entity_type="deal", entity_id=d.id)
    return _deal(d)


class DealPatch(BaseModel):
    status: Literal["open", "won", "lost"] | None = None
    value: Decimal | None = Field(None, ge=0, le=100_000_000)
    title: str | None = Field(None, max_length=300)
    notes: str | None = Field(None, max_length=2000)


@router.patch("/deals/{did}")
def patch_deal(did: int, body: DealPatch, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    d = db.get(Deal, did)
    if d is None:
        raise HTTPException(404, "Deal not found")
    data = body.model_dump(exclude_unset=True)
    if "value" in data:
        d.value_amount = data.pop("value")
    for k, v in data.items():
        setattr(d, k, v)
    if body.status in ("won", "lost"):
        try:
            svc.move(db, d.contact_id, body.status, auth.label, f"deal '{d.title}' marked {body.status}")
        except svc.PipelineError:
            pass
    audit(db, "deal.update", actor=auth.label, entity_type="deal", entity_id=d.id, details={"status": body.status})
    return _deal(d)
