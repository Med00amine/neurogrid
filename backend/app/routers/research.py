from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import pricing
from app.core.deps import Auth, read_access, write_access
from app.core.events import audit
from app.core.settings_store import get_group
from app.db import get_db
from app.jobs import queue
from app.models import (
    Company,
    Contact,
    FindingEvidence,
    ResearchFinding,
    ResearchRun,
    ResearchSource,
)
from app.services import contacts as contacts_svc
from app.services import jurisdictions
from app.services.eligibility import evaluate_contact
from app.services.research import pipeline
from app.services.suppression import find_active

router = APIRouter(prefix="/api", tags=["research"])
MAX_COMPANIES_PER_JOB = 300


def _run(r: ResearchRun) -> dict:
    return {
        "id": r.id, "status": r.status, "mode": r.mode, "model": r.model, "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None, "pages_fetched": r.pages_fetched,
        "search_queries": r.search_queries, "input_tokens": r.llm_input_tokens, "output_tokens": r.llm_output_tokens,
        "cost_usd_est": float(r.cost_usd_est or 0), "summary": r.summary, "company_type": r.company_type,
        "uncertainties": r.uncertainties, "error": r.error,
        "cache_expires_at": r.cache_expires_at.isoformat() if r.cache_expires_at else None,
    }


@router.get("/companies")
def list_companies(search: str | None = None, researched: bool | None = None, company_type: str | None = None,
                   page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                   db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    latest = (select(ResearchRun.company_id, func.max(ResearchRun.id).label("rid")).where(ResearchRun.status.in_(("completed", "partial")))
              .group_by(ResearchRun.company_id).subquery())
    ncontacts = select(Contact.company_id, func.count().label("n")).group_by(Contact.company_id).subquery()
    stmt = (select(Company, latest.c.rid, ncontacts.c.n).outerjoin(latest, latest.c.company_id == Company.id)
            .outerjoin(ncontacts, ncontacts.c.company_id == Company.id))
    cstmt = select(func.count()).select_from(Company).outerjoin(latest, latest.c.company_id == Company.id)
    conds = []
    if search:
        t = "%" + search.strip().replace("%", "").replace("_", "") + "%"
        conds.append(Company.name.ilike(t) | Company.domain.ilike(t))
    if researched is not None:
        conds.append(latest.c.rid.is_not(None) if researched else latest.c.rid.is_(None))
    if company_type:
        conds.append(Company.company_type == company_type)
    for c in conds:
        stmt, cstmt = stmt.where(c), cstmt.where(c)
    rows = db.execute(stmt.order_by(Company.id.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    run_ids = [rid for _, rid, _ in rows if rid]
    runs = {r.id: r for r in db.execute(select(ResearchRun).where(ResearchRun.id.in_(run_ids))).scalars()} if run_ids else {}
    items = [{"id": c.id, "name": c.name, "domain": c.domain, "website_url": c.website_url, "country": c.country_code,
              "company_type": c.company_type, "contacts": n or 0,
              "research": {"run_id": rid, "at": runs[rid].finished_at.isoformat() if runs[rid].finished_at else None, "mode": runs[rid].mode} if rid and rid in runs else None}
             for c, rid, n in rows]
    return {"items": items, "total": db.execute(cstmt).scalar_one(), "page": page}


@router.get("/companies/{company_id}")
def company_detail(company_id: int, run_id: int | None = None, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    c = db.get(Company, company_id)
    if c is None:
        raise HTTPException(404, "Company not found")
    runs = db.execute(select(ResearchRun).where(ResearchRun.company_id == company_id).order_by(ResearchRun.id.desc()).limit(20)).scalars().all()
    chosen = next((r for r in runs if r.id == run_id), None) if run_id else next((r for r in runs if r.status in ("completed", "partial")), None)
    sources, findings = [], []
    if chosen:
        srcs = db.execute(select(ResearchSource).where(ResearchSource.run_id == chosen.id).order_by(ResearchSource.id)).scalars().all()
        sources = [{"id": s.id, "url": s.url, "final_url": s.final_url, "title": s.title, "type": s.source_type, "http_status": s.http_status,
                    "retrieved_at": s.retrieved_at.isoformat(), "injection_flag": s.injection_flag, "chars": len(s.text or ""), "note": s.note} for s in srcs]
        fs = db.execute(select(ResearchFinding).where(ResearchFinding.run_id == chosen.id).order_by(ResearchFinding.id)).scalars().all()
        evs = db.execute(select(FindingEvidence).where(FindingEvidence.finding_id.in_([f.id for f in fs]))).scalars().all() if fs else []
        by_f: dict[int, list] = {}
        for e in evs:
            by_f.setdefault(e.finding_id, []).append({"id": e.id, "source_id": e.source_id, "excerpt": e.excerpt, "verified": e.excerpt_verified})
        findings = [{"id": f.id, "category": f.category, "statement": f.statement, "edited_statement": f.edited_statement, "kind": f.kind,
                     "confidence": f.confidence, "status": f.status, "verified": f.verified, "service_tags": f.service_tags,
                     "review_note": f.review_note, "reviewed_by": f.reviewed_by, "evidence": by_f.get(f.id, [])} for f in fs]
    contacts = db.execute(select(Contact).where(Contact.company_id == company_id).order_by(Contact.id).limit(100)).scalars().all()
    return {
        "id": c.id, "name": c.name, "domain": c.domain, "website_url": c.website_url, "country": c.country_code,
        "company_type": c.company_type, "notes": c.notes,
        "contacts": [{"id": x.id, "email": x.email_normalized, "name": x.name, "stage": x.pipeline_stage, "role_title": x.role_title,
                      "eligibility_status": x.eligibility_status, "country": x.country_code} for x in contacts],
        "runs": [_run(r) for r in runs], "run": _run(chosen) if chosen else None, "sources": sources, "findings": findings,
    }


@router.get("/research/sources/{source_id}")
def source_text(source_id: int, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    s = db.get(ResearchSource, source_id)
    if s is None:
        raise HTTPException(404, "Source not found")
    return {"id": s.id, "url": s.url, "title": s.title, "text": s.text, "retrieved_at": s.retrieved_at.isoformat(), "injection_flag": s.injection_flag}


class CompanyPatch(BaseModel):
    name: str | None = Field(None, max_length=300)
    website_url: str | None = Field(None, max_length=600)
    company_type: Literal["ai_consultancy", "software_agency", "aws_consultancy", "product_company", "other"] | None = None
    notes: str | None = Field(None, max_length=4000)


@router.patch("/companies/{company_id}")
def patch_company(company_id: int, body: CompanyPatch, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    c = db.get(Company, company_id)
    if c is None:
        raise HTTPException(404, "Company not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("website_url"):
        u = urlsplit(data["website_url"].strip())
        if u.scheme not in ("http", "https") or not u.hostname or u.username:
            raise HTTPException(400, "Website must be a plain http(s) URL")
        data["website_url"] = f"{u.scheme}://{u.netloc}{u.path}".rstrip("/")
    for k, v in data.items():
        setattr(c, k, v or None)
    audit(db, "company.patch", actor=auth.label, entity_type="company", entity_id=c.id, details={k: v for k, v in data.items() if k != "notes"})
    return {"ok": True}


class StartBody(BaseModel):
    contact_ids: list[int] = Field(default_factory=list)
    company_ids: list[int] = Field(default_factory=list)
    filter: dict = Field(default_factory=dict)
    force: bool = False


def _estimate(db: Session, n: int) -> dict:
    rcfg, lcfg = get_group(db, "research"), get_group(db, "llm")
    from app.services import llm as llm_mod

    uses_llm = rcfg.use_llm and lcfg.provider == "anthropic" and llm_mod.get_llm(db) is not None
    llm_cost = pricing.llm_cost(lcfg.research_model, 8000, 1200) * n if uses_llm else 0
    search_cost = pricing.search_cost("tavily", 1) * n if rcfg.search_provider == "tavily" else 0
    return {
        "companies": n, "mode": "llm" if uses_llm else "heuristic", "model": lcfg.research_model if uses_llm else None,
        "estimated_llm_usd": float(llm_cost), "estimated_search_usd": float(search_cost),
        "estimated_total_usd": float(llm_cost) + float(search_cost),
        "assumption": "About 8,000 input and 1,200 output tokens per company at the price table in Settings; 1 search credit per company if search is on. Real usage is recorded per run.",
    }


@router.post("/research/estimate")
def estimate(body: StartBody, db: Session = Depends(get_db), _: Auth = Depends(write_access)):
    return _estimate(db, len(_company_ids(db, body)[0]))


def _company_ids(db: Session, body: StartBody) -> tuple[list[int], int]:
    ids = set(body.company_ids)
    skipped = 0
    cids = list(body.contact_ids)
    if not cids and body.filter:
        f = contacts_svc.ContactFilter(**{k: v for k, v in body.filter.items() if k in contacts_svc.ContactFilter.__dataclass_fields__ and k != "ids"})
        cids = contacts_svc.resolve_ids(db, f)
    if cids:
        rows = db.execute(select(Contact).where(Contact.id.in_(cids))).scalars().all()
        for c in rows:
            if c.company_id is None or c.is_freemail or c.eligibility_status == "excluded" or find_active(db, c.email_normalized):
                skipped += 1  # no company site to research, personal mailbox, excluded, or suppressed
            else:
                ids.add(c.company_id)
    return sorted(ids), skipped


@router.post("/research/start")
def start(body: StartBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    ids, skipped = _company_ids(db, body)
    if not ids:
        raise HTTPException(400, "No researchable companies in the selection (excluded, suppressed, free-mail and company-less contacts are skipped)")
    if len(ids) > MAX_COMPANIES_PER_JOB:
        raise HTTPException(400, f"Research at most {MAX_COMPANIES_PER_JOB} companies per job; start with a small test batch")
    est = _estimate(db, len(ids))
    job = queue.enqueue(db, "research_companies", {"company_ids": ids, "force": body.force}, title=f"Research {len(ids)} compan{'y' if len(ids) == 1 else 'ies'}",
                        created_by=auth.label, total=len(ids))
    audit(db, "research.start", actor=auth.label, entity_type="job", entity_id=job.id, details={"companies": len(ids), "force": body.force})
    return {"job_id": str(job.id), "companies": len(ids), "skipped_contacts": skipped, "estimate": est}


class ReviewBody(BaseModel):
    action: Literal["approve", "reject", "edit", "reset"]
    edited_statement: str | None = Field(None, max_length=1500)
    note: str | None = Field(None, max_length=1000)


@router.post("/research/findings/{finding_id}/review")
def review(finding_id: int, body: ReviewBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        f = pipeline.review_finding(db, finding_id, body.action, auth.label, edited_statement=body.edited_statement, note=body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(db, f"research.finding.{body.action}", actor=auth.label, entity_type="finding", entity_id=f.id, details={"company_id": f.company_id})
    return {"id": f.id, "status": f.status, "edited_statement": f.edited_statement}


class BulkApprove(BaseModel):
    categories: list[str] | None = None


@router.post("/research/runs/{run_id}/approve-verified")
def approve_verified(run_id: int, body: BulkApprove, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    """Approve every still-pending FACT whose quotes were all verified. Inferences always need an explicit click."""
    stmt = select(ResearchFinding).where(ResearchFinding.run_id == run_id, ResearchFinding.status == "pending",
                                         ResearchFinding.verified.is_(True), ResearchFinding.kind == "fact")
    if body.categories:
        stmt = stmt.where(ResearchFinding.category.in_(body.categories))
    n = 0
    for f in db.execute(stmt).scalars().all():
        pipeline.review_finding(db, f.id, "approve", auth.label)
        n += 1
    audit(db, "research.approve_verified", actor=auth.label, entity_type="research_run", entity_id=run_id, details={"approved": n})
    return {"approved": n}


class CountryBody(BaseModel):
    country: str = Field(min_length=2, max_length=2)
    reason: str = Field(min_length=5, max_length=500)


@router.post("/companies/{company_id}/apply-country")
def apply_country(company_id: int, body: CountryBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    """Set the country on this company's contacts that have no manually-set country."""
    code = body.country.upper()
    if not code.isalpha():
        raise HTTPException(400, "country must be a 2-letter code")
    jurisdictions.ensure_policy_rows(db, {code})
    c = db.get(Company, company_id)
    if c is None:
        raise HTTPException(404, "Company not found")
    c.country_code = code
    n = db.execute(update(Contact).where(Contact.company_id == company_id, Contact.country_source.in_(("tld", "import")) | Contact.country_code.is_(None))
                   .values(country_code=code, country_source="research")).rowcount
    audit(db, "company.apply_country", actor=auth.label, entity_type="company", entity_id=company_id, details={"country": code, "reason": body.reason, "contacts": n})
    return {"updated_contacts": n}
