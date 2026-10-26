"""Suppression list, jurisdiction policies and the audit/event trail."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.constants import JURISDICTION_RULES, SUPPRESSION_REASONS
from app.core.deps import Auth, read_access, write_access
from app.db import get_db
from app.models import AuditLog, Contact, JurisdictionPolicy, OutreachEvent, Suppression
from app.services import jurisdictions, suppression

router = APIRouter(prefix="/api", tags=["compliance"])


def _ser(s: Suppression) -> dict:
    return {
        "id": s.id, "kind": s.kind, "value": s.value, "reason": s.reason, "source": s.source, "active": s.active,
        "notes": s.notes, "created_at": s.created_at.isoformat(), "created_by": s.created_by, "contact_id": s.contact_id,
        "deactivated_at": s.deactivated_at.isoformat() if s.deactivated_at else None, "deactivated_by": s.deactivated_by,
        "deactivate_reason": s.deactivate_reason, "protected": s.reason in suppression.PROTECTED_REASONS,
    }


@router.get("/suppressions")
def list_suppressions(search: str | None = None, active: bool | None = True, reason: str | None = None,
                      page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                      db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    stmt, cstmt = select(Suppression), select(func.count()).select_from(Suppression)
    for cond in ([Suppression.value.ilike(f"%{search.strip().lower()}%")] if search else []) + \
                ([Suppression.active.is_(active)] if active is not None else []) + \
                ([Suppression.reason == reason] if reason else []):
        stmt, cstmt = stmt.where(cond), cstmt.where(cond)
    rows = db.execute(stmt.order_by(Suppression.id.desc()).limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return {"items": [_ser(s) for s in rows], "total": db.execute(cstmt).scalar_one()}


class SuppressBody(BaseModel):
    kind: Literal["email", "domain"] = "email"
    value: str = Field(max_length=320)
    reason: Literal["manual", "legal", "do_not_contact", "unsubscribe", "complaint"] = "manual"
    notes: str | None = Field(None, max_length=1000)


@router.post("/suppressions")
def add_suppression(body: SuppressBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        s = suppression.suppress(db, kind=body.kind, value=body.value, reason=body.reason, source="manual entry",
                                 actor=auth.label, notes=body.notes)
    except suppression.SuppressionError as e:
        raise HTTPException(400, str(e))
    return _ser(s)


class LiftBody(BaseModel):
    reason: str = Field(max_length=1000)
    confirm_value: str = Field(max_length=320)
    force: bool = False


@router.post("/suppressions/{sid}/lift")
def lift(sid: int, body: LiftBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        return _ser(suppression.deactivate(db, sid, actor=auth.label, reason=body.reason, confirm_value=body.confirm_value, force=body.force))
    except suppression.SuppressionError as e:
        raise HTTPException(400, str(e))


@router.get("/suppressions/check")
def check(email: str, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    from app.core.text import normalize_email

    e = normalize_email(email)
    if not e:
        raise HTTPException(400, "Not a valid email address")
    s = suppression.find_active(db, e)
    return {"email": e, "suppressed": s is not None, "entry": _ser(s) if s else None}


# --- jurisdictions -------------------------------------------------------------------------------------------------

def _pol(p: JurisdictionPolicy, contacts: int = 0) -> dict:
    return {
        "country_code": p.country_code, "name": p.name, "rule": p.rule, "notes": p.notes, "source_urls": p.source_urls,
        "sources_checked_on": p.sources_checked_on.isoformat() if p.sources_checked_on else None,
        "attested": p.attested_at is not None, "attested_at": p.attested_at.isoformat() if p.attested_at else None,
        "attested_by": p.attested_by, "contacts": contacts,
    }


@router.get("/jurisdictions")
def list_policies(db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    counts = dict(db.execute(select(func.coalesce(Contact.country_code, "ZZ"), func.count()).group_by(Contact.country_code)).all())
    rows = db.execute(select(JurisdictionPolicy)).scalars().all()
    out = [_pol(p, counts.get(p.country_code, 0)) for p in rows]
    out.sort(key=lambda p: -p["contacts"])
    return {"items": out, "disclaimer": "These rules are a starting point drawn from the sources shown, not legal advice."}


class RuleBody(BaseModel):
    rule: Literal["consent_required", "opt_out_permitted", "unknown"]
    notes: str | None = Field(None, max_length=2000)


@router.put("/jurisdictions/{code}/rule")
def set_rule(code: str, body: RuleBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        return _pol(jurisdictions.set_rule(db, code.upper(), body.rule, actor=auth.label, notes=body.notes))
    except ValueError as e:
        raise HTTPException(400, str(e))


class AttestBody(BaseModel):
    note: str = Field(min_length=10, max_length=1000)
    confirm: bool = False


@router.post("/jurisdictions/{code}/attest")
def attest(code: str, body: AttestBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    if not body.confirm:
        raise HTTPException(400, "Tick the confirmation: you are stating that you have assessed and meet this country's requirements")
    try:
        return _pol(jurisdictions.attest(db, code.upper(), actor=auth.label, note=body.note))
    except ValueError as e:
        raise HTTPException(400, str(e))


# --- audit trail -----------------------------------------------------------------------------------------------------

@router.get("/events")
def events(contact_id: int | None = None, campaign_id: int | None = None, event_type: str | None = None,
           page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=500),
           db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    stmt = select(OutreachEvent)
    if contact_id:
        stmt = stmt.where(OutreachEvent.contact_id == contact_id)
    if campaign_id:
        stmt = stmt.where(OutreachEvent.campaign_id == campaign_id)
    if event_type:
        stmt = stmt.where(OutreachEvent.event_type == event_type)
    rows = db.execute(stmt.order_by(OutreachEvent.id.desc()).limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return [{"id": e.id, "at": e.occurred_at.isoformat(), "type": e.event_type, "contact_id": e.contact_id, "message_id": e.message_id,
             "campaign_id": e.campaign_id, "source": e.source, "actor": e.actor, "data": e.data} for e in rows]


@router.get("/audit")
def audit_log(action: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=500),
              db: Session = Depends(get_db), auth: Auth = Depends(read_access)):
    if not auth.is_admin:
        raise HTTPException(403, "The audit log needs the admin role")
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.like(action + "%"))
    rows = db.execute(stmt.order_by(AuditLog.id.desc()).limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return [{"id": a.id, "at": a.at.isoformat(), "actor": a.actor_label, "action": a.action, "entity_type": a.entity_type,
             "entity_id": a.entity_id, "details": a.details} for a in rows]
