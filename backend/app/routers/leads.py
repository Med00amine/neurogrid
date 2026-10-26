from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import POSITIONINGS
from app.core.deps import Auth, read_access, write_access
from app.core.events import audit
from app.db import get_db
from app.models import Contact, FindingEvidence, LeadScore, ResearchFinding, Segment
from app.services import contacts as contacts_svc
from app.services import scoring, segments

router = APIRouter(prefix="/api", tags=["leads"])


@router.get("/leads/summary")
def summary(db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    buckets = {f"{lo}-{lo + 9}": 0 for lo in range(0, 100, 10)}
    for total, n in db.execute(select(LeadScore.total, func.count()).group_by(LeadScore.total)).all():
        lo = min(total // 10 * 10, 90)
        buckets[f"{lo}-{lo + 9}"] += n
    bands = {"strong evidence": 0, "some evidence": 0, "weak evidence": 0}
    for total, n in db.execute(select(LeadScore.total, func.count()).group_by(LeadScore.total)).all():
        bands[scoring.band(total)] += n
    scored = sum(bands.values())
    return {"histogram": [{"range": k, "count": v} for k, v in buckets.items()], "bands": bands, "scored_contacts": scored,
            "disclaimer": scoring.DISCLAIMER}


@router.get("/leads/{contact_id}")
def lead_detail(contact_id: int, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    c = db.get(Contact, contact_id)
    if c is None:
        raise HTTPException(404, "Contact not found")
    live = scoring.compute(db, c)
    fids = sorted({i for comp in live["components"].values() for i in comp["finding_ids"]})
    fs = db.execute(select(ResearchFinding).where(ResearchFinding.id.in_(fids))).scalars().all() if fids else []
    evs = db.execute(select(FindingEvidence).where(FindingEvidence.finding_id.in_(fids))).scalars().all() if fids else []
    by_f: dict[int, list] = {}
    for e in evs:
        by_f.setdefault(e.finding_id, []).append({"source_id": e.source_id, "excerpt": e.excerpt, "verified": e.excerpt_verified})
    return {**live, "findings": {f.id: {"category": f.category, "statement": f.edited_statement or f.statement, "kind": f.kind,
                                         "evidence": by_f.get(f.id, [])} for f in fs}}


@router.post("/leads/recompute")
def recompute(db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    ids = db.execute(select(Contact.id).join(LeadScore, LeadScore.contact_id == Contact.id)).scalars().all()
    n = 0
    for cid in ids[:20000]:
        scoring.recompute_contact(db, db.get(Contact, cid))
        n += 1
    return {"recomputed": n}


# --- segments ------------------------------------------------------------------------------------------------------------

def _seg(db: Session, s: Segment) -> dict:
    clause = segments.clause_from_filter(s.filter or {})
    stmt = contacts_svc.base_select(func.count(Contact.id))
    if clause is not None:
        stmt = stmt.where(clause)
    return {"id": s.id, "name": s.name, "description": s.description, "positioning": s.positioning, "filter": s.filter,
            "is_active": s.is_active, "members": db.execute(stmt).scalar_one()}


@router.get("/segments")
def list_segments(db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    return [_seg(db, s) for s in db.execute(select(Segment).order_by(Segment.id)).scalars()]


class SegmentBody(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str | None = Field(None, max_length=1000)
    positioning: str = "direct_solution"
    filter: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


@router.post("/segments")
def create_segment(body: SegmentBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    if body.positioning not in POSITIONINGS:
        raise HTTPException(400, "unknown positioning")
    try:
        flt = segments.validate_filter(body.filter)
    except segments.SegmentError as e:
        raise HTTPException(400, str(e))
    if db.execute(select(Segment).where(Segment.name == body.name)).scalar_one_or_none():
        raise HTTPException(409, "A segment with this name already exists")
    s = Segment(name=body.name, description=body.description, positioning=body.positioning, filter=flt, is_active=body.is_active)
    db.add(s)
    db.flush()
    audit(db, "segment.create", actor=auth.label, entity_type="segment", entity_id=s.id)
    return _seg(db, s)


@router.put("/segments/{sid}")
def update_segment(sid: int, body: SegmentBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    s = db.get(Segment, sid)
    if s is None:
        raise HTTPException(404, "Segment not found")
    try:
        flt = segments.validate_filter(body.filter)
    except segments.SegmentError as e:
        raise HTTPException(400, str(e))
    s.name, s.description, s.positioning, s.filter, s.is_active = body.name, body.description, body.positioning, flt, body.is_active
    audit(db, "segment.update", actor=auth.label, entity_type="segment", entity_id=s.id)
    return _seg(db, s)


@router.delete("/segments/{sid}")
def delete_segment(sid: int, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    s = db.get(Segment, sid)
    if s is None:
        raise HTTPException(404, "Segment not found")
    db.delete(s)
    audit(db, "segment.delete", actor=auth.label, entity_type="segment", entity_id=sid)
    return {"ok": True}
