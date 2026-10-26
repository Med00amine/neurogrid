from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import LENGTHS, POSITIONINGS, TONES
from app.core import pricing
from app.core.deps import Auth, read_access, write_access
from app.core.events import audit
from app.core.settings_store import get_group
from app.db import get_db
from app.jobs import queue
from app.models import Company, Contact, Draft, DraftVersion, EmailTemplate, ResearchSource
from app.services import contacts as contacts_svc
from app.services import llm as llm_mod
from app.services.drafting import compose, generator, service

router = APIRouter(prefix="/api", tags=["studio"])


def _template(t: EmailTemplate) -> dict:
    return {"id": t.id, "name": t.name, "positioning": t.positioning, "tone": t.tone, "length": t.length, "instructions": t.instructions,
            "subject_hint": t.subject_hint, "cta_text": t.cta_text, "is_default": t.is_default}


@router.get("/templates")
def list_templates(db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    return [_template(t) for t in db.execute(select(EmailTemplate).order_by(EmailTemplate.id)).scalars()]


class TemplateBody(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    positioning: Literal["agency_capacity", "direct_solution"] = "direct_solution"
    tone: Literal["direct", "friendly", "formal"] = "direct"
    length: Literal["short", "medium"] = "short"
    instructions: str | None = Field(None, max_length=3000)
    subject_hint: str | None = Field(None, max_length=200)
    cta_text: str | None = Field(None, max_length=400)


@router.post("/templates")
def create_template(body: TemplateBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    if db.execute(select(EmailTemplate).where(EmailTemplate.name == body.name)).scalar_one_or_none():
        raise HTTPException(409, "A template with this name already exists")
    t = EmailTemplate(**body.model_dump())
    db.add(t)
    db.flush()
    audit(db, "template.create", actor=auth.label, entity_type="template", entity_id=t.id)
    return _template(t)


@router.put("/templates/{tid}")
def update_template(tid: int, body: TemplateBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    t = db.get(EmailTemplate, tid)
    if t is None:
        raise HTTPException(404, "Template not found")
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    audit(db, "template.update", actor=auth.label, entity_type="template", entity_id=t.id)
    return _template(t)


@router.delete("/templates/{tid}")
def delete_template(tid: int, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    t = db.get(EmailTemplate, tid)
    if t is None:
        raise HTTPException(404, "Template not found")
    db.delete(t)
    audit(db, "template.delete", actor=auth.label, entity_type="template", entity_id=tid)
    return {"ok": True}


# --- drafts -----------------------------------------------------------------------------------------------------------------


def _row(d: Draft, v: DraftVersion | None, c: Contact, comp: Company | None) -> dict:
    return {
        "id": d.id, "status": d.status, "step": d.step, "contact": {"id": c.id, "email": c.email_normalized, "name": c.name},
        "company": {"id": comp.id, "name": comp.name or c.company_name_raw} if comp else {"id": None, "name": c.company_name_raw},
        "subject": v.subject if v else None, "source": v.source if v else None, "version": v.version_no if v else None,
        "blockers": len(v.blockers) if v else 0, "warnings": len(v.warnings) if v else 0,
        "cost_usd_est": float(v.cost_usd_est or 0) if v else 0, "updated_at": d.updated_at.isoformat(),
        "approved_by": d.approved_by, "approved_at": d.approved_at.isoformat() if d.approved_at else None,
    }


@router.get("/drafts")
def list_drafts(status: str | None = None, contact_id: int | None = None, search: str | None = None,
                page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    stmt = (select(Draft, DraftVersion, Contact, Company).join(Contact, Contact.id == Draft.contact_id)
            .outerjoin(DraftVersion, DraftVersion.id == Draft.current_version_id).outerjoin(Company, Company.id == Contact.company_id))
    cstmt = select(func.count()).select_from(Draft).join(Contact, Contact.id == Draft.contact_id).outerjoin(Company, Company.id == Contact.company_id)
    conds = []
    if status:
        conds.append(Draft.status == status)
    if contact_id:
        conds.append(Draft.contact_id == contact_id)
    if search:
        t = "%" + search.strip().replace("%", "") + "%"
        conds.append(Contact.email_normalized.ilike(t) | Contact.name.ilike(t) | Company.name.ilike(t))
    for c in conds:
        stmt, cstmt = stmt.where(c), cstmt.where(c)
    rows = db.execute(stmt.order_by(Draft.id.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    counts = dict(db.execute(select(Draft.status, func.count()).group_by(Draft.status)).all())
    return {"items": [_row(d, v, c, comp) for d, v, c, comp in rows], "total": db.execute(cstmt).scalar_one(), "counts": counts}


@router.get("/drafts/{draft_id}")
def draft_detail(draft_id: int, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    d = db.get(Draft, draft_id)
    if d is None:
        raise HTTPException(404, "Draft not found")
    c = db.get(Contact, d.contact_id)
    comp = db.get(Company, c.company_id) if c.company_id else None
    v = db.get(DraftVersion, d.current_version_id)
    versions = db.execute(select(DraftVersion).where(DraftVersion.draft_id == d.id).order_by(DraftVersion.version_no.desc())).scalars().all()
    usable = generator.usable_findings(db, c.company_id)
    cited = {f for p in v.claims for f in p.get("finding_ids", [])}
    src_ids = {e.source_id for u in usable for e in u.evidence if e.source_id}
    srcs = {s.id: s for s in db.execute(select(ResearchSource).where(ResearchSource.id.in_(src_ids))).scalars()} if src_ids else {}
    sender = get_group(db, "sender")
    env = get_settings()
    stmts, proof = generator.sender_statements(sender)
    comp_msg = compose.compose(sender=sender, env=env, subject=v.subject, core_body=v.body_text, message_id=0, contact_id=c.id,
                               recipient_email=c.email_normalized, rfc_message_id="<preview@example.invalid>")
    preview_body = comp_msg.body
    lcfg = get_group(db, "llm")
    return {
        "id": d.id, "status": d.status, "step": d.step, "template_id": d.template_id,
        "approval_valid": service.is_approval_valid(db, d),
        "contact": {"id": c.id, "email": c.email_normalized, "name": c.name, "role_title": c.role_title, "country": c.country_code,
                    "stage": c.pipeline_stage, "eligibility_status": c.eligibility_status},
        "company": {"id": comp.id, "name": comp.name or c.company_name_raw, "domain": comp.domain, "website_url": comp.website_url} if comp else None,
        "version": {"id": v.id, "no": v.version_no, "subject": v.subject, "body": v.body_text, "claims": v.claims, "warnings": v.warnings,
                    "blockers": v.blockers, "source": v.source, "model": v.model, "input_tokens": v.input_tokens, "output_tokens": v.output_tokens,
                    "cost_usd_est": float(v.cost_usd_est or 0), "created_by": v.created_by, "created_at": v.created_at.isoformat()},
        "versions": [{"id": x.id, "no": x.version_no, "source": x.source, "created_at": x.created_at.isoformat(), "created_by": x.created_by} for x in versions],
        "findings": [{"id": u.finding.id, "category": u.finding.category, "kind": u.finding.kind, "confidence": u.finding.confidence,
                      "statement": u.text, "cited": u.finding.id in cited,
                      "evidence": [{"excerpt": e.excerpt, "verified": e.excerpt_verified,
                                    "source": ({"id": srcs[e.source_id].id, "url": srcs[e.source_id].url, "title": srcs[e.source_id].title,
                                                "retrieved_at": srcs[e.source_id].retrieved_at.isoformat()} if e.source_id in srcs else None)} for e in u.evidence]} for u in usable],
        "sender_statements": [{"index": i, "text": s, "is_proof_point": i in proof} for i, s in enumerate(stmts)],
        "preview": {"from": comp_msg.from_header, "subject": comp_msg.subject, "body": preview_body, "headers": comp_msg.headers},
        "estimates": {"regenerate_usd": float(pricing.estimate_draft_cost(lcfg.draft_model)), "model": lcfg.draft_model, "ai_available": llm_mod.get_llm(db) is not None},
    }


class GenerateBody(BaseModel):
    contact_ids: list[int] = Field(default_factory=list)
    filter: dict = Field(default_factory=dict)
    template_id: int
    step: int = Field(1, ge=1, le=3)
    tone: Literal["direct", "friendly", "formal"] | None = None
    length: Literal["short", "medium"] | None = None
    use_llm: bool = True


def _contact_ids(db: Session, body: GenerateBody) -> list[int]:
    ids = list(body.contact_ids)
    if not ids and body.filter:
        f = contacts_svc.ContactFilter.from_dict(body.filter)
        ids = contacts_svc.resolve_ids(db, f)
    return ids


@router.post("/drafts/estimate")
def estimate(body: GenerateBody, db: Session = Depends(get_db), _: Auth = Depends(write_access)):
    n = len(_contact_ids(db, body))
    lcfg = get_group(db, "llm")
    ai = body.use_llm and llm_mod.get_llm(db) is not None
    per = pricing.estimate_draft_cost(lcfg.draft_model) if ai else 0
    return {"drafts": n, "mode": "llm" if ai else "template", "model": lcfg.draft_model if ai else None, "per_draft_usd": float(per), "total_usd": float(per) * n,
            "assumption": "About 4,000 input and 700 output tokens per draft at the Settings price table."}


@router.post("/drafts/generate")
def generate(body: GenerateBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    ids = _contact_ids(db, body)
    if not ids:
        raise HTTPException(400, "Select at least one contact")
    if len(ids) > 200:
        raise HTTPException(400, "Generate at most 200 drafts per job")
    if db.get(EmailTemplate, body.template_id) is None:
        raise HTTPException(404, "Template not found")
    job = queue.enqueue(db, "generate_drafts", {"contact_ids": ids, "template_id": body.template_id, "step": body.step, "tone": body.tone,
                                                 "length": body.length, "use_llm": body.use_llm, "actor": auth.label},
                        title=f"Generate {len(ids)} draft(s)", created_by=auth.label, total=len(ids))
    return {"job_id": str(job.id), "contacts": len(ids)}


class EditBody(BaseModel):
    subject: str = Field(max_length=300)
    body: str = Field(max_length=20000)


@router.post("/drafts/{draft_id}/edit")
def edit(draft_id: int, body: EditBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        v = service.edit(db, draft_id, auth.label, subject=body.subject, body=body.body)
    except service.DraftError as e:
        raise HTTPException(409, str(e))
    return {"version": v.version_no, "warnings": v.warnings, "blockers": v.blockers}


class RegenBody(BaseModel):
    template_id: int | None = None
    tone: Literal["direct", "friendly", "formal"] | None = None
    length: Literal["short", "medium"] | None = None
    use_llm: bool = True


@router.post("/drafts/{draft_id}/regenerate")
def regenerate(draft_id: int, body: RegenBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        v = service.regenerate(db, draft_id, auth.label, template_id=body.template_id, tone=body.tone, length=body.length, use_llm=body.use_llm)
    except service.DraftError as e:
        raise HTTPException(409, str(e))
    except llm_mod.LLMError as e:
        raise HTTPException(502 if e.category != "budget_exceeded" else 402, str(e))
    return {"version": v.version_no, "warnings": v.warnings, "blockers": v.blockers}


class ApproveBody(BaseModel):
    acknowledge_warnings: bool = False


@router.post("/drafts/{draft_id}/approve")
def approve(draft_id: int, body: ApproveBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        d = service.approve(db, draft_id, auth.label, acknowledge_warnings=body.acknowledge_warnings)
    except service.DraftError as e:
        raise HTTPException(409, str(e))
    return {"status": d.status}


class RejectBody(BaseModel):
    reason: str | None = Field(None, max_length=500)


@router.post("/drafts/{draft_id}/reject")
def reject(draft_id: int, body: RejectBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        d = service.reject(db, draft_id, auth.label, body.reason)
    except service.DraftError as e:
        raise HTTPException(409, str(e))
    return {"status": d.status}
