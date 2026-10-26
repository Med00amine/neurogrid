from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import ELIGIBILITY_STATUSES, PIPELINE_STAGES
from app.core.deps import Auth, read_access, write_access
from app.core.events import audit, record_event
from app.db import get_db
from app.models import (
    Company,
    Contact,
    Deal,
    Draft,
    EligibilityDecision,
    InboundMessage,
    LeadScore,
    Message,
    Note,
    OutreachEvent,
    ResearchRun,
    Task,
)
from app.services import contacts as svc
from app.services import jurisdictions

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


def filters(
    search: str | None = None, eligibility: str | None = None, history: str | None = None, stage: str | None = None,
    country: str | None = None, company_type: str | None = None, suppressed: bool | None = None,
    researched: bool | None = None, min_score: int | None = None, import_batch_id: int | None = None,
    role_account: bool | None = None, segment_id: int | None = None,
) -> svc.ContactFilter:
    return svc.ContactFilter(search=search, eligibility=eligibility, history=history, stage=stage, country=country,
                             company_type=company_type, suppressed=suppressed, researched=researched, min_score=min_score,
                             import_batch_id=import_batch_id, role_account=role_account, segment_id=segment_id)


@router.get("")
def list_contacts(
    f: svc.ContactFilter = Depends(filters), sort: str = "created", desc: bool = True,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), _: Auth = Depends(read_access),
):
    return svc.list_contacts(db, f, sort=sort, desc=desc, page=page, page_size=page_size)


@router.get("/facets")
def facets(db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    return svc.facets(db)


@router.get("/export")
def export(format: Literal["csv", "xlsx"] = "csv", f: svc.ContactFilter = Depends(filters), db: Session = Depends(get_db),
           auth: Auth = Depends(read_access)):
    if not auth.is_admin:
        raise HTTPException(403, "Exporting personal data needs the admin role")
    audit(db, "contacts.export", actor=auth.label, details={"format": format})
    if format == "xlsx":
        data = svc.export_xlsx(db, f)
        mt, name = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "contacts.xlsx"
    else:
        data = svc.export_csv(db, f)
        mt, name = "text/csv; charset=utf-8", "contacts.csv"
    return Response(content=data, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{name}"'})


class BulkBody(BaseModel):
    action: Literal["set_eligibility", "attest_history", "set_country", "record_consent", "set_stage"]
    value: str | None = None
    reason: str = ""
    ids: list[int] = Field(default_factory=list)
    filter: dict = Field(default_factory=dict)
    expected_count: int | None = None


@router.post("/bulk")
def bulk(body: BulkBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    f = svc.ContactFilter.from_dict(body.filter)
    f.ids = body.ids
    if not body.ids and not any(v not in (None, "", []) for v in body.filter.values()):
        raise HTTPException(400, "Select contacts or apply at least one filter first (bulk actions never run on 'everything' by accident)")
    try:
        if body.expected_count is not None:
            n = len(svc.resolve_ids(db, f))
            if n != body.expected_count:
                raise HTTPException(409, f"The selection changed: you confirmed {body.expected_count} but {n} match now. Reload and confirm again.")
        return svc.bulk_action(db, f, body.action, body.value, body.reason, auth.label)
    except svc.BulkError as e:
        raise HTTPException(400, str(e))


@router.get("/{contact_id}")
def detail(contact_id: int, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    c = db.get(Contact, contact_id)
    if c is None:
        raise HTTPException(404, "Contact not found")
    company = db.get(Company, c.company_id) if c.company_id else None
    score = db.get(LeadScore, c.id)
    run = None
    if c.company_id:
        run = db.execute(select(ResearchRun).where(ResearchRun.company_id == c.company_id, ResearchRun.status.in_(("completed", "partial")))
                         .order_by(ResearchRun.id.desc()).limit(1)).scalar_one_or_none()
    from app.services.suppression import find_active

    sup = find_active(db, c.email_normalized)
    policy = jurisdictions.policy_for(db, c.country_code)
    msgs = db.execute(select(Message).where(Message.contact_id == c.id).order_by(Message.id.desc()).limit(50)).scalars().all()
    events = db.execute(select(OutreachEvent).where(OutreachEvent.contact_id == c.id).order_by(OutreachEvent.id.desc()).limit(100)).scalars().all()
    decisions = db.execute(select(EligibilityDecision).where(EligibilityDecision.contact_id == c.id).order_by(EligibilityDecision.id.desc()).limit(50)).scalars().all()
    replies = db.execute(select(InboundMessage).where(InboundMessage.matched_contact_id == c.id).order_by(InboundMessage.id.desc()).limit(20)).scalars().all()
    notes = db.execute(select(Note).where(Note.contact_id == c.id).order_by(Note.id.desc())).scalars().all()
    tasks = db.execute(select(Task).where(Task.contact_id == c.id).order_by(Task.id.desc())).scalars().all()
    deals = db.execute(select(Deal).where(Deal.contact_id == c.id).order_by(Deal.id.desc())).scalars().all()
    drafts = db.execute(select(Draft).where(Draft.contact_id == c.id).order_by(Draft.id.desc()).limit(10)).scalars().all()
    base = svc.serialize_contact(c, company, score.total if score else None, sup is not None, run is not None)
    base.update(
        {
            "email_original": c.email_original,
            "attributes": c.attributes,
            "source_row": c.source_row,
            "import_batch_id": c.import_batch_id,
            "consent_basis": c.consent_basis,
            "consent_recorded_at": c.consent_recorded_at.isoformat() if c.consent_recorded_at else None,
            "role_title_source": c.role_title_source,
            "suppression": {"id": sup.id, "kind": sup.kind, "reason": sup.reason, "created_at": sup.created_at.isoformat()} if sup else None,
            "policy": {"country_code": policy.country_code, "name": policy.name, "rule": policy.rule, "attested": policy.attested_at is not None, "notes": policy.notes},
            "can_send": svc.explain(db, c),
            "score_detail": {"total": score.total, "components": score.components, "weights": score.weights, "computed_at": score.computed_at.isoformat()} if score else None,
            "research_run": {"id": run.id, "finished_at": run.finished_at.isoformat() if run.finished_at else None, "summary": run.summary, "mode": run.mode} if run else None,
            "messages": [{"id": m.id, "campaign_id": m.campaign_id, "step": m.sequence_step, "status": m.status, "is_dry_run": m.is_dry_run,
                          "subject": m.subject, "submitted_at": m.submitted_at.isoformat() if m.submitted_at else None, "reason": m.status_reason} for m in msgs],
            "events": [{"id": e.id, "at": e.occurred_at.isoformat(), "type": e.event_type, "source": e.source, "actor": e.actor, "data": e.data} for e in events],
            "decisions": [{"at": d.decided_at.isoformat(), "by": d.decided_by, "field": d.field, "from": d.from_value, "to": d.to_value, "reason": d.reason} for d in decisions],
            "replies": [{"id": r.id, "subject": r.subject, "received_at": r.received_at.isoformat() if r.received_at else None, "classification": r.classification} for r in replies],
            "notes": [{"id": n.id, "body": n.body, "by": n.created_by, "at": n.created_at.isoformat()} for n in notes],
            "tasks": [{"id": t.id, "title": t.title, "kind": t.kind, "due_at": t.due_at.isoformat() if t.due_at else None, "status": t.status, "cancel_reason": t.cancel_reason} for t in tasks],
            "deals": [{"id": d.id, "title": d.title, "value": float(d.value_amount) if d.value_amount is not None else None, "currency": d.currency, "status": d.status} for d in deals],
            "drafts": [{"id": d.id, "status": d.status, "step": d.step} for d in drafts],
        }
    )
    return base


class ContactPatch(BaseModel):
    role_title: str | None = Field(None, max_length=200)
    role_title_source: str | None = Field(None, max_length=300)
    name: str | None = Field(None, max_length=300)


@router.patch("/{contact_id}")
def patch(contact_id: int, body: ContactPatch, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    c = db.get(Contact, contact_id)
    if c is None:
        raise HTTPException(404, "Contact not found")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(c, k, (v or None) if isinstance(v, str) else v)
    audit(db, "contact.patch", actor=auth.label, entity_type="contact", entity_id=c.id, details=data)
    return {"ok": True}


class EligibilityBody(BaseModel):
    status: Literal["eligible", "excluded", "review_required", "unreviewed"]
    reason: str = Field("", max_length=1000)


@router.post("/{contact_id}/eligibility")
def set_eligibility(contact_id: int, body: EligibilityBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    c = db.get(Contact, contact_id)
    if c is None:
        raise HTTPException(404, "Contact not found")
    if len(body.reason.strip()) < 10:
        raise HTTPException(400, "Write a reason (10+ characters); it is kept in the audit trail")
    if body.status == "eligible" and c.is_freemail:
        raise HTTPException(400, "Personal free-mail addresses cannot be made eligible")
    before = c.eligibility_status
    c.eligibility_status = body.status
    c.eligibility_reason = body.reason.strip()
    c.eligibility_updated_at = datetime.now(timezone.utc)
    db.add(EligibilityDecision(contact_id=c.id, decided_by=auth.label, field="eligibility_status", from_value=before, to_value=body.status, reason=body.reason.strip()))
    record_event(db, "eligibility_changed", contact_id=c.id, source="user", actor=auth.label, data={"from": before, "to": body.status})
    return {"ok": True}
