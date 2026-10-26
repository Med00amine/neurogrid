from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import REPLY_CLASSES
from app.core.deps import Auth, read_access, write_access
from app.db import get_db
from app.jobs import queue
from app.models import Campaign, Company, Contact, InboundMessage, MailboxCursor, Message
from app.services.replies import processor
from app.services.replies.parse import new_text_only
from app.services.replies.imap import get_mailbox

router = APIRouter(prefix="/api", tags=["replies"])


def _row(r: InboundMessage, c: Contact | None, comp: Company | None, m: Message | None) -> dict:
    return {
        "id": r.id, "kind": r.kind, "from_email": r.from_email, "from_name": r.from_name, "subject": r.subject,
        "received_at": r.received_at.isoformat() if r.received_at else None, "classification": r.classification,
        "classification_source": r.classification_source, "confidence": r.classification_confidence, "reviewed": r.reviewed,
        "match_method": r.match_method, "snippet": new_text_only(r.body_text or "")[:160],
        "contact": {"id": c.id, "name": c.name, "email": c.email_normalized, "stage": c.pipeline_stage} if c else None,
        "company": comp.name if comp else None, "message_id": m.id if m else None, "campaign_id": m.campaign_id if m else None,
        "dsn": r.dsn or None,
    }


@router.get("/replies")
def list_replies(classification: str | None = None, kind: str | None = None, reviewed: bool | None = None, search: str | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                 db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    stmt = (select(InboundMessage, Contact, Company, Message).outerjoin(Contact, Contact.id == InboundMessage.matched_contact_id)
            .outerjoin(Company, Company.id == Contact.company_id).outerjoin(Message, Message.id == InboundMessage.matched_message_id))
    cstmt = select(func.count()).select_from(InboundMessage).outerjoin(Contact, Contact.id == InboundMessage.matched_contact_id)
    conds = []
    if classification:
        conds.append(InboundMessage.classification == classification)
    if kind:
        conds.append(InboundMessage.kind == kind)
    if reviewed is not None:
        conds.append(InboundMessage.reviewed.is_(reviewed))
    if search:
        t = "%" + search.strip().replace("%", "") + "%"
        conds.append(InboundMessage.subject.ilike(t) | InboundMessage.from_email.ilike(t) | Contact.name.ilike(t))
    for c in conds:
        stmt, cstmt = stmt.where(c), cstmt.where(c)
    rows = db.execute(stmt.order_by(InboundMessage.received_at.desc().nulls_last(), InboundMessage.id.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    counts = dict(db.execute(select(InboundMessage.classification, func.count()).group_by(InboundMessage.classification)).all())
    return {"items": [_row(r, c, comp, m) for r, c, comp, m in rows], "total": db.execute(cstmt).scalar_one(),
            "counts": {str(k): v for k, v in counts.items()}, "classes": list(REPLY_CLASSES)}


@router.get("/replies/status")
def status(db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    env = get_settings()
    cur = db.get(MailboxCursor, env.imap_mailbox)
    return {"imap_configured": bool(env.imap_host and env.imap_password), "mailbox": env.imap_mailbox, "host": env.imap_host,
            "last_checked": cur.updated_at.isoformat() if cur else None, "last_uid": cur.last_uid if cur else None,
            "note": "Replies and bounce notifications are read from your mailbox (read-only). Nothing is moved or deleted."}


@router.get("/replies/{rid}")
def detail(rid: int, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    r = db.get(InboundMessage, rid)
    if r is None:
        raise HTTPException(404, "Reply not found")
    c = db.get(Contact, r.matched_contact_id) if r.matched_contact_id else None
    comp = db.get(Company, c.company_id) if c and c.company_id else None
    m = db.get(Message, r.matched_message_id) if r.matched_message_id else None
    out = _row(r, c, comp, m)
    out.update({"body": r.body_text, "new_text": new_text_only(r.body_text or ""), "reasons": r.classification_reasons,
                "original": {"id": m.id, "subject": m.subject, "body": m.body_text, "submitted_at": m.submitted_at.isoformat() if m.submitted_at else None} if m else None,
                "in_reply_to": r.in_reply_to, "references": r.references_header,
                "weak_match": bool(r.match_method and "weak" in r.match_method)})
    return out


class ClassifyBody(BaseModel):
    classification: Literal["interested", "referral", "info_request", "not_interested", "unsubscribe", "auto_response", "uncertain"]
    mark_lost: bool = False


@router.post("/replies/{rid}/classify")
def classify(rid: int, body: ClassifyBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        r = processor.apply_user_classification(db, rid, body.classification, auth.label, mark_lost=body.mark_lost)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": r.id, "classification": r.classification, "source": r.classification_source}


@router.post("/replies/poll")
def poll_now(db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    if get_mailbox() is None:
        raise HTTPException(409, "IMAP is not configured. Add IMAP_HOST / IMAP_USERNAME / IMAP_PASSWORD to .env and restart.")
    import time

    job = queue.enqueue(db, "poll_replies", {}, title="Check mailbox for replies and bounces", idempotency_key=f"poll_replies_manual:{int(time.time() // 20)}",
                        created_by=auth.label, priority=10, max_retries=1)
    return {"job_id": str(job.id)}


@router.post("/replies/test-imap")
def test_imap(auth: Auth = Depends(write_access)):
    c = get_mailbox()
    if c is None:
        raise HTTPException(409, "IMAP is not configured in .env")
    return c.check()


@router.post("/messages/{mid}/check-mailbox")
def check_mailbox(mid: int, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    """Look for this message's Message-ID in your Sent folder / inbox. Informational only: you still choose the resolution."""
    m = db.get(Message, mid)
    if m is None:
        raise HTTPException(404, "Message not found")
    c = get_mailbox()
    env = get_settings()
    if c is None:
        raise HTTPException(409, "IMAP is not configured, so the Sent folder cannot be searched. Check it manually.")
    boxes = [b for b in (env.imap_sent_mailbox, env.imap_mailbox) if b]
    found = c.find_by_message_id(m.rfc_message_id, boxes)
    return {"message_id": m.rfc_message_id, "searched": boxes, "found": found,
            "hint": "Found in the Sent folder: it was most likely sent." if any(f["mailbox"] == env.imap_sent_mailbox for f in found) else
                    "Not found. Absence from the Sent folder suggests it was not sent, but not every provider saves a copy."}
