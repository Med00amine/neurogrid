"""Draft persistence, linting in context, versioning and approval."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.events import audit, record_event
from app.core.settings_store import get_group
from app.models import Company, Contact, Draft, DraftVersion, EmailTemplate
from app.services.drafting import generator, lint

ACTIVE = ("generating", "needs_review", "approved")


class DraftError(ValueError):
    pass


def content_hash(subject: str, body: str) -> str:
    return hashlib.sha256(json.dumps([subject.strip(), body.strip()], ensure_ascii=False).encode()).hexdigest()


def _context_lint(db: Session, draft: Draft, contact: Contact, subject: str, paragraphs: list[dict], template: EmailTemplate | None,
                  usable: list[generator.Usable]) -> tuple[list[dict], list[dict]]:
    sender = get_group(db, "sender")
    stmts, proof_refs = generator.sender_statements(sender)
    env = get_settings()
    body = generator.body_from_paragraphs(paragraphs)
    others = []
    rows = db.execute(
        select(DraftVersion.body_text, Contact.name, Contact.company_id)
        .join(Draft, Draft.current_version_id == DraftVersion.id).join(Contact, Contact.id == Draft.contact_id)
        .where(Draft.id != draft.id, Draft.status.in_(("needs_review", "approved")), Draft.step == draft.step)
        .order_by(DraftVersion.id.desc()).limit(80)
    ).all()
    for b, n, cid in rows:
        others.append((n or "another contact", b, cid == contact.company_id))
    issues = lint.lint(
        subject=subject, body=body, paragraphs=paragraphs, allowed_urls=[u for u in sender.verified_urls],
        sender_emails=[e for e in (sender.from_email, sender.reply_to, env.smtp_from_email) if e],
        usable_findings={u.finding.id: u.blob() for u in usable}, proof_point_refs=proof_refs,
        length=(template.length if template else "short"), other_bodies=others,
    )
    # context warnings (missing information / confidence)
    if not contact.role_title:
        issues.append(lint.Issue("role_unknown", "The recipient's role is unknown; the email makes no assumption about it"))
    if not contact.name or len(contact.name.split()) != 2:
        issues.append(lint.Issue("name_check", f"Check the greeting: the stored name is “{contact.name or 'empty'}”"))
    cited_ids = {f for p in paragraphs for f in p.get("finding_ids", [])}
    cited = [u for u in usable if u.finding.id in cited_ids]
    if cited and all(u.finding.confidence == "low" for u in cited):
        issues.append(lint.Issue("low_confidence", "Every cited finding has low confidence"))
    if any(u.finding.kind == "inference" for u in cited):
        issues.append(lint.Issue("inference_used", "The email rests partly on an inference (not a stated fact); check the wording is tentative"))
    if not sender.display_name or not sender.postal_address or not sender.data_source_statement:
        issues.append(lint.Issue("sender_incomplete", "Sender identity, postal address or data-source line is missing in Settings; required before any live send"))
    blockers = [i.asdict() for i in issues if i.severity == "blocker"]
    warnings = [i.asdict() for i in issues if i.severity != "blocker"]
    return warnings, blockers


def add_version(db: Session, draft: Draft, contact: Contact, *, subject: str, paragraphs: list[dict], source: str, actor: str,
                model: str | None = None, input_tokens: int = 0, output_tokens: int = 0, cost=0, extra_warnings: list[str] | None = None) -> DraftVersion:
    template = db.get(EmailTemplate, draft.template_id) if draft.template_id else None
    usable = generator.usable_findings(db, contact.company_id)
    warnings, blockers = _context_lint(db, draft, contact, subject, paragraphs, template, usable)
    for w in extra_warnings or []:
        warnings.append({"code": "note", "text": w, "severity": "warning"})
    body = generator.body_from_paragraphs(paragraphs)
    n = (db.execute(select(DraftVersion.version_no).where(DraftVersion.draft_id == draft.id).order_by(DraftVersion.version_no.desc()).limit(1)).scalar_one_or_none() or 0) + 1
    v = DraftVersion(draft_id=draft.id, version_no=n, subject=subject.strip()[:300], body_text=body, claims=paragraphs, warnings=warnings, blockers=blockers,
                     source=source, model=model, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd_est=cost,
                     content_hash=content_hash(subject, body), created_by=actor)
    db.add(v)
    db.flush()
    draft.current_version_id = v.id
    # any new content voids a previous approval
    if draft.status == "approved":
        record_event(db, "draft_approval_voided", contact_id=contact.id, source="user", actor=actor, data={"draft_id": draft.id, "version": n})
    draft.approved_version_id, draft.approved_hash, draft.approved_by, draft.approved_at = None, None, None, None
    draft.status = "needs_review"
    db.flush()
    return v


def create_draft(db: Session, contact: Contact, template: EmailTemplate, actor: str, *, step: int = 1, tone: str | None = None,
                 length: str | None = None, use_llm: bool = True, llm_client=None, job_id=None, segment_id: int | None = None) -> Draft:
    existing = db.execute(select(Draft).where(Draft.contact_id == contact.id, Draft.step == step, Draft.status.in_(ACTIVE))).scalar_one_or_none()
    if existing:
        raise DraftError("This contact already has an active draft; open it and use Regenerate")
    gen, _ = generator.generate(db, contact, template, step=step, tone=tone, length=length, use_llm=use_llm, llm_client=llm_client, job_id=job_id)
    draft = Draft(contact_id=contact.id, template_id=template.id, segment_id=segment_id, step=step, status="needs_review", created_by=actor)
    db.add(draft)
    db.flush()
    add_version(db, draft, contact, subject=gen.subject, paragraphs=gen.paragraphs, source=gen.source, actor=actor, model=gen.model,
                input_tokens=gen.input_tokens, output_tokens=gen.output_tokens, cost=gen.cost_usd, extra_warnings=gen.notes)
    if contact.pipeline_stage in ("new", "researched", "qualified"):
        record_event(db, "stage_changed", contact_id=contact.id, source="system", actor=actor, data={"from": contact.pipeline_stage, "to": "draft_ready"})
        contact.pipeline_stage = "draft_ready"
    record_event(db, "draft_created", contact_id=contact.id, source="user", actor=actor, data={"draft_id": draft.id, "source": gen.source, "step": step})
    return draft


def regenerate(db: Session, draft_id: int, actor: str, *, template_id: int | None = None, tone: str | None = None, length: str | None = None,
               use_llm: bool = True, llm_client=None) -> DraftVersion:
    draft = db.get(Draft, draft_id)
    if draft is None or draft.status not in ACTIVE:
        raise DraftError("Draft not found or no longer active")
    contact = db.get(Contact, draft.contact_id)
    template = db.get(EmailTemplate, template_id or draft.template_id)
    if template is None:
        raise DraftError("Template not found")
    draft.template_id = template.id
    gen, _ = generator.generate(db, contact, template, step=draft.step, tone=tone, length=length, use_llm=use_llm, llm_client=llm_client)
    v = add_version(db, draft, contact, subject=gen.subject, paragraphs=gen.paragraphs, source=gen.source, actor=actor, model=gen.model,
                    input_tokens=gen.input_tokens, output_tokens=gen.output_tokens, cost=gen.cost_usd, extra_warnings=gen.notes)
    audit(db, "draft.regenerate", actor=actor, entity_type="draft", entity_id=draft.id, details={"version": v.version_no})
    return v


def edit(db: Session, draft_id: int, actor: str, *, subject: str, body: str) -> DraftVersion:
    draft = db.get(Draft, draft_id)
    if draft is None or draft.status not in ACTIVE:
        raise DraftError("Draft not found or no longer active")
    cur = db.get(DraftVersion, draft.current_version_id)
    contact = db.get(Contact, draft.contact_id)
    if content_hash(subject, body) == cur.content_hash:
        return cur
    paragraphs = generator.split_into_paragraphs(body, cur.claims)
    v = add_version(db, draft, contact, subject=subject, paragraphs=paragraphs, source="user", actor=actor)
    audit(db, "draft.edit", actor=actor, entity_type="draft", entity_id=draft.id, details={"version": v.version_no})
    return v


def approve(db: Session, draft_id: int, actor: str, *, acknowledge_warnings: bool) -> Draft:
    draft = db.get(Draft, draft_id)
    if draft is None or draft.status not in ("needs_review", "approved"):
        raise DraftError("Draft not found or not awaiting review")
    v = db.get(DraftVersion, draft.current_version_id)
    contact = db.get(Contact, draft.contact_id)
    # re-lint right now: evidence may have been rejected since the draft was written
    template = db.get(EmailTemplate, draft.template_id) if draft.template_id else None
    usable = generator.usable_findings(db, contact.company_id)
    warnings, blockers = _context_lint(db, draft, contact, v.subject, v.claims, template, usable)
    v.warnings, v.blockers = warnings, blockers
    if blockers:
        raise DraftError("Cannot approve: " + "; ".join(b["text"] for b in blockers))
    serious = [w for w in warnings if w["code"] not in ("role_unknown", "name_check")]
    if serious and not acknowledge_warnings:
        raise DraftError("This draft has warnings. Review them and confirm you accept them to approve.")
    draft.status = "approved"
    draft.approved_version_id = v.id
    draft.approved_hash = v.content_hash
    draft.approved_by = actor
    draft.approved_at = datetime.now(timezone.utc)
    if contact.pipeline_stage in ("new", "researched", "qualified", "draft_ready"):
        record_event(db, "stage_changed", contact_id=contact.id, source="system", actor=actor, data={"from": contact.pipeline_stage, "to": "approved"})
        contact.pipeline_stage = "approved"
    record_event(db, "draft_approved", contact_id=contact.id, source="user", actor=actor,
                 data={"draft_id": draft.id, "version": v.version_no, "hash": v.content_hash[:12], "acknowledged_warnings": [w["code"] for w in warnings]})
    audit(db, "draft.approve", actor=actor, entity_type="draft", entity_id=draft.id, details={"version": v.version_no})
    db.flush()
    return draft


def reject(db: Session, draft_id: int, actor: str, reason: str | None = None) -> Draft:
    draft = db.get(Draft, draft_id)
    if draft is None or draft.status not in ("needs_review", "approved"):
        raise DraftError("Draft not found or not awaiting review")
    draft.status = "rejected"
    draft.approved_version_id = draft.approved_hash = None
    contact = db.get(Contact, draft.contact_id)
    record_event(db, "draft_rejected", contact_id=contact.id, source="user", actor=actor, data={"draft_id": draft.id, "reason": reason})
    if contact.pipeline_stage == "approved":
        contact.pipeline_stage = "qualified"
    db.flush()
    return draft


def is_approval_valid(db: Session, draft: Draft) -> bool:
    """True only if the approved text is byte-for-byte the current text."""
    if draft.status != "approved" or not draft.approved_version_id or draft.approved_version_id != draft.current_version_id:
        return False
    v = db.get(DraftVersion, draft.approved_version_id)
    return v is not None and v.content_hash == draft.approved_hash and content_hash(v.subject, v.body_text) == draft.approved_hash
