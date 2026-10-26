"""Contact queries, bulk actions and exports."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import openpyxl
from sqlalchemy import Select, and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.constants import ELIGIBILITY_STATUSES, HISTORY_STATUSES, PIPELINE_STAGES
from app.core.events import audit, record_event
from app.core.text import csv_safe
from app.models import (
    Company,
    Contact,
    EligibilityDecision,
    LeadScore,
    ResearchRun,
    Suppression,
)
from app.services import jurisdictions
from app.services.eligibility import evaluate_contact

MAX_BULK = 50_000


@dataclass
class ContactFilter:
    search: str | None = None
    eligibility: str | None = None
    history: str | None = None
    stage: str | None = None
    country: str | None = None
    company_type: str | None = None
    suppressed: bool | None = None
    researched: bool | None = None
    min_score: int | None = None
    import_batch_id: int | None = None
    role_account: bool | None = None
    segment_id: int | None = None
    ids: list[int] = field(default_factory=list)


def suppressed_expr():
    return or_(
        exists().where(and_(Suppression.active.is_(True), Suppression.kind == "email", Suppression.value == Contact.email_normalized)),
        exists().where(and_(Suppression.active.is_(True), Suppression.kind == "domain", Suppression.value == Contact.email_domain)),
    )


def researched_expr():
    return exists().where(
        and_(ResearchRun.company_id == Contact.company_id, ResearchRun.status.in_(("completed", "partial")))
    )


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def apply_filters(stmt: Select, f: ContactFilter, db: Session | None = None) -> Select:
    if f.ids:
        stmt = stmt.where(Contact.id.in_(f.ids))
    if f.search:
        t = _like(f.search.strip())
        stmt = stmt.where(
            or_(
                Contact.email_normalized.ilike(t, escape="\\"),
                Contact.name.ilike(t, escape="\\"),
                Contact.company_name_raw.ilike(t, escape="\\"),
                Company.name.ilike(t, escape="\\"),
            )
        )
    if f.eligibility:
        stmt = stmt.where(Contact.eligibility_status == f.eligibility)
    if f.history:
        stmt = stmt.where(Contact.history_status == f.history)
    if f.stage:
        stmt = stmt.where(Contact.pipeline_stage == f.stage)
    if f.country:
        stmt = stmt.where(Contact.country_code.is_(None) if f.country == "ZZ" else Contact.country_code == f.country)
    if f.company_type:
        stmt = stmt.where(Company.company_type == f.company_type)
    if f.suppressed is not None:
        stmt = stmt.where(suppressed_expr() if f.suppressed else ~suppressed_expr())
    if f.researched is not None:
        stmt = stmt.where(researched_expr() if f.researched else ~researched_expr())
    if f.min_score is not None:
        stmt = stmt.where(LeadScore.total >= f.min_score)
    if f.import_batch_id:
        stmt = stmt.where(Contact.import_batch_id == f.import_batch_id)
    if f.role_account is not None:
        stmt = stmt.where(Contact.is_role_account.is_(f.role_account))
    if f.segment_id and db is not None:
        from app.services.segments import segment_clause

        clause = segment_clause(db, f.segment_id)
        if clause is not None:
            stmt = stmt.where(clause)
    return stmt


SORTS = {
    "created": Contact.id,
    "email": Contact.email_normalized,
    "name": Contact.name,
    "company": Company.name,
    "score": LeadScore.total,
    "stage": Contact.pipeline_stage,
    "eligibility": Contact.eligibility_status,
    "country": Contact.country_code,
}


def base_select(*cols):
    return (
        select(*cols)
        .select_from(Contact)
        .outerjoin(Company, Company.id == Contact.company_id)
        .outerjoin(LeadScore, LeadScore.contact_id == Contact.id)
    )


def list_contacts(db: Session, f: ContactFilter, *, sort: str = "created", desc: bool = True, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    page_size = max(1, min(page_size, 200))
    total = db.execute(apply_filters(base_select(func.count(Contact.id)), f, db)).scalar_one()
    col = SORTS.get(sort, Contact.id)
    order = col.desc().nulls_last() if desc else col.asc().nulls_last()
    stmt = apply_filters(
        base_select(Contact, Company, LeadScore.total.label("score"), suppressed_expr().label("is_suppressed"), researched_expr().label("researched")),
        f, db,
    ).order_by(order, Contact.id).limit(page_size).offset((max(page, 1) - 1) * page_size)
    items = []
    for contact, company, score, is_supp, researched in db.execute(stmt).all():
        items.append(serialize_contact(contact, company, score, is_supp, researched))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def serialize_contact(c: Contact, company: Company | None, score: int | None = None, is_suppressed: bool = False, researched: bool = False) -> dict:
    return {
        "id": c.id,
        "email": c.email_normalized,
        "name": c.name,
        "company": {"id": company.id, "name": company.name or c.company_name_raw, "domain": company.domain, "type": company.company_type} if company else {"id": None, "name": c.company_name_raw, "domain": c.email_domain, "type": None},
        "country": c.country_code,
        "country_source": c.country_source,
        "eligibility_status": c.eligibility_status,
        "eligibility_reason": c.eligibility_reason,
        "history_status": c.history_status,
        "pipeline_stage": c.pipeline_stage,
        "is_suppressed": bool(is_suppressed),
        "is_freemail": c.is_freemail,
        "is_role_account": c.is_role_account,
        "researched": bool(researched),
        "score": score,
        "role_title": c.role_title,
        "has_consent": bool(c.consent_basis),
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def facets(db: Session) -> dict[str, Any]:
    """Counts used by the Contacts page filter bar."""
    def count_by(col):
        return {k if k is not None else "ZZ": n for k, n in db.execute(select(col, func.count()).group_by(col)).all()}

    return {
        "eligibility": count_by(Contact.eligibility_status),
        "history": count_by(Contact.history_status),
        "stage": count_by(Contact.pipeline_stage),
        "country": dict(sorted(count_by(Contact.country_code).items(), key=lambda kv: -kv[1])[:40]),
    }


# --- bulk actions -------------------------------------------------------------------------------------------------------


class BulkError(ValueError):
    pass


def resolve_ids(db: Session, f: ContactFilter) -> list[int]:
    stmt = apply_filters(base_select(Contact.id), f, db).order_by(Contact.id).limit(MAX_BULK + 1)
    ids = list(db.execute(stmt).scalars())
    if len(ids) > MAX_BULK:
        raise BulkError(f"Selection is larger than {MAX_BULK:,} contacts; narrow the filter")
    return ids


def _decisions(db: Session, ids: list[int], field_name: str, from_to: dict[int, tuple[str | None, str]], actor: str, reason: str) -> None:
    rows = [dict(contact_id=cid, decided_by=actor, field=field_name, from_value=fv, to_value=tv, reason=reason) for cid, (fv, tv) in from_to.items()]
    for i in range(0, len(rows), 5000):
        db.execute(pg_insert(EligibilityDecision).values(rows[i : i + 5000]))


def bulk_action(db: Session, f: ContactFilter, action: str, value: str | None, reason: str, actor: str) -> dict[str, Any]:
    reason = (reason or "").strip()
    ids = resolve_ids(db, f)
    if not ids:
        return {"matched": 0, "changed": 0, "skipped": 0, "skipped_reasons": {}}
    now = datetime.now(timezone.utc)
    skipped: dict[str, int] = {}
    changed = 0

    if action == "set_eligibility":
        if value not in ELIGIBILITY_STATUSES or value == "unreviewed":
            raise BulkError("value must be eligible, excluded or review_required")
        if len(reason) < 10:
            raise BulkError("Write a reason (10+ characters): it is stored in the audit trail")
        rows = db.execute(select(Contact.id, Contact.eligibility_status, Contact.is_freemail).where(Contact.id.in_(ids))).all()
        sup = set(db.execute(select(Contact.id).where(Contact.id.in_(ids), suppressed_expr())).scalars())
        target, trail = [], {}
        for cid, status, freemail in rows:
            if value == "eligible" and freemail:
                skipped["free-mail address"] = skipped.get("free-mail address", 0) + 1
            elif value == "eligible" and cid in sup:
                skipped["suppressed"] = skipped.get("suppressed", 0) + 1
            elif status == value:
                skipped["already " + value] = skipped.get("already " + value, 0) + 1
            else:
                target.append(cid)
                trail[cid] = (status, value)
        if target:
            db.execute(update(Contact).where(Contact.id.in_(target)).values(eligibility_status=value, eligibility_reason=reason, eligibility_updated_at=now))
            _decisions(db, target, "eligibility_status", trail, actor, reason)
        changed = len(target)

    elif action == "attest_history":
        if len(reason) < 10:
            raise BulkError("Describe how you know these contacts were never emailed (10+ characters)")
        rows = db.execute(select(Contact.id, Contact.history_status).where(Contact.id.in_(ids))).all()
        target, trail = [], {}
        for cid, st in rows:
            if st == "unknown":
                target.append(cid)
                trail[cid] = (st, "attested_unsent")
            else:
                skipped[f"history is already '{st}'"] = skipped.get(f"history is already '{st}'", 0) + 1
        if target:
            db.execute(update(Contact).where(Contact.id.in_(target)).values(history_status="attested_unsent"))
            _decisions(db, target, "history_status", trail, actor, reason)
        changed = len(target)

    elif action == "set_country":
        code = (value or "").strip().upper()
        if len(code) != 2 or not code.isalpha():
            raise BulkError("country must be a 2-letter code, e.g. FR")
        if len(reason) < 5:
            raise BulkError("Add a short reason, e.g. 'company imprint shows France'")
        jurisdictions.ensure_policy_rows(db, {code})
        rows = db.execute(select(Contact.id, Contact.country_code).where(Contact.id.in_(ids))).all()
        target = [cid for cid, cc in rows if cc != code]
        trail = {cid: (cc, code) for cid, cc in rows if cc != code}
        skipped["already " + code] = len(rows) - len(target)
        if target:
            db.execute(update(Contact).where(Contact.id.in_(target)).values(country_code=code, country_source="manual"))
            _decisions(db, target, "country_code", trail, actor, reason)
        changed = len(target)

    elif action == "record_consent":
        if len(reason) < 10:
            raise BulkError("Describe the consent (when/how it was given, 10+ characters)")
        db.execute(update(Contact).where(Contact.id.in_(ids)).values(consent_basis=reason, consent_recorded_at=now))
        _decisions(db, ids, "consent_basis", {cid: (None, "recorded") for cid in ids}, actor, reason)
        changed = len(ids)

    elif action == "set_stage":
        if value not in PIPELINE_STAGES:
            raise BulkError("unknown stage")
        if value == "do_not_contact":
            raise BulkError("Use the suppression list to mark someone do-not-contact")
        db.execute(update(Contact).where(Contact.id.in_(ids)).values(pipeline_stage=value))
        for cid in ids[:5000]:
            record_event(db, "stage_changed", contact_id=cid, source="user", actor=actor, data={"to": value, "bulk": True})
        changed = len(ids)
    else:
        raise BulkError(f"unknown action {action!r}")

    audit(db, f"contacts.bulk.{action}", actor=actor, entity_type="contacts", details={"matched": len(ids), "changed": changed, "value": value, "reason": reason})
    return {"matched": len(ids), "changed": changed, "skipped": sum(skipped.values()), "skipped_reasons": skipped}


# --- export (formula-injection safe) --------------------------------------------------------------------------------------

EXPORT_COLUMNS = ["email", "name", "company", "country", "eligibility_status", "history_status", "pipeline_stage", "suppressed", "score"]


def export_rows(db: Session, f: ContactFilter):
    stmt = apply_filters(
        base_select(Contact, Company, LeadScore.total, suppressed_expr()), f, db
    ).order_by(Contact.id)
    for c, comp, score, supp in db.execute(stmt.execution_options(yield_per=2000)):
        yield [
            c.email_normalized, c.name, (comp.name if comp and comp.name else c.company_name_raw), c.country_code,
            c.eligibility_status, c.history_status, c.pipeline_stage, "yes" if supp else "no", score,
        ]


def export_csv(db: Session, f: ContactFilter) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLUMNS)
    for row in export_rows(db, f):
        w.writerow([csv_safe(v) for v in row])
    return buf.getvalue().encode("utf-8-sig")


def export_xlsx(db: Session, f: ContactFilter) -> bytes:
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("contacts")
    ws.append(EXPORT_COLUMNS)
    for row in export_rows(db, f):
        ws.append([csv_safe(v) for v in row])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def explain(db: Session, contact: Contact, step: int = 1) -> dict:
    d = evaluate_contact(db, contact, step=step)
    return {"ok": d.ok, "reasons": [{"code": r.code, "text": r.text} for r in d.reasons]}
