from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.deps import Auth, read_access, write_access
from app.core.settings_store import get_group
from app.db import get_db
from app.models import Campaign, CampaignRecipient, Contact, Message, OutreachEvent, SendAttempt
from app.services import eligibility
from app.services.sending import campaigns as svc
from app.services.sending import engine, limits
from app.services.sending.providers import SMTPProvider, get_provider

router = APIRouter(prefix="/api", tags=["campaigns"])


def _ser(db: Session, c: Campaign, detail: bool = False) -> dict:
    counts = dict(db.execute(select(Message.status, func.count()).where(Message.campaign_id == c.id).group_by(Message.status)).all())
    rec = dict(db.execute(select(CampaignRecipient.status, func.count()).where(CampaignRecipient.campaign_id == c.id).group_by(CampaignRecipient.status)).all())
    out = {
        "id": c.id, "name": c.name, "mode": c.mode, "status": c.status, "step": c.sequence_step, "provider": c.provider,
        "daily_cap": c.daily_cap, "hourly_cap": c.hourly_cap, "min_interval_seconds": c.min_interval_seconds, "window": c.window,
        "estimate": c.estimate, "paused_reason": c.paused_reason, "created_by": c.created_by, "created_at": c.created_at.isoformat(),
        "confirmed_by": c.confirmed_by, "confirmed_at": c.confirmed_at.isoformat() if c.confirmed_at else None,
        "finished_at": c.finished_at.isoformat() if c.finished_at else None,
        "backoff_until": c.backoff_until.isoformat() if c.backoff_until else None,
        "message_counts": counts, "recipient_counts": {"included": rec.get("included", 0), "excluded": rec.get("excluded", 0)},
        "segment_id": c.segment_id, "required_phrase": svc.required_phrase(c, rec.get("included", 0)),
    }
    if detail:
        env = get_settings()
        out["readiness"] = [p.__dict__ for p in eligibility.sender_readiness(db, env, live=c.mode == "live")]
        out["live_gate"] = engine.live_gates(db, c) if c.mode == "live" else None
        if c.status == "running":
            g = limits.send_gate(db, c, datetime.now(timezone.utc))
            out["gate"] = {"allowed": g.allowed, "reason": g.reason, "retry_at": g.retry_at.isoformat() if g.retry_at else None}
        out["sender_snapshot"] = c.sender_snapshot
    return out


@router.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    return [_ser(db, c) for c in db.execute(select(Campaign).order_by(Campaign.id.desc()).limit(100)).scalars()]


class CampaignBody(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    mode: Literal["dry_run", "live"] = "dry_run"
    step: int = Field(1, ge=1, le=3)
    contact_ids: list[int] = Field(default_factory=list)
    filter: dict = Field(default_factory=dict)
    segment_id: int | None = None
    daily_cap: int | None = Field(None, ge=1, le=500)
    hourly_cap: int | None = Field(None, ge=1, le=100)
    min_interval_seconds: int | None = Field(None, ge=10, le=3600)
    notes: str | None = Field(None, max_length=2000)


@router.post("/campaigns")
def create(body: CampaignBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        c = svc.build(db, name=body.name, mode=body.mode, actor=auth.label, step=body.step, contact_ids=body.contact_ids, filter=body.filter,
                      segment_id=body.segment_id, daily_cap=body.daily_cap, hourly_cap=body.hourly_cap,
                      min_interval_seconds=body.min_interval_seconds, notes=body.notes)
    except svc.CampaignError as e:
        raise HTTPException(400, str(e))
    return _ser(db, c, True)


@router.get("/campaigns/{cid}")
def detail(cid: int, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    c = db.get(Campaign, cid)
    if c is None:
        raise HTTPException(404, "Campaign not found")
    return _ser(db, c, True)


@router.get("/campaigns/{cid}/recipients")
def recipients(cid: int, status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
               db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    stmt = select(CampaignRecipient, Contact).join(Contact, Contact.id == CampaignRecipient.contact_id).where(CampaignRecipient.campaign_id == cid)
    cstmt = select(func.count()).select_from(CampaignRecipient).where(CampaignRecipient.campaign_id == cid)
    if status:
        stmt, cstmt = stmt.where(CampaignRecipient.status == status), cstmt.where(CampaignRecipient.status == status)
    rows = db.execute(stmt.order_by(CampaignRecipient.id).limit(page_size).offset((page - 1) * page_size)).all()
    msgs = {m.contact_id: m for m in db.execute(select(Message).where(Message.campaign_id == cid, Message.contact_id.in_([c.id for _, c in rows]))).scalars()} if rows else {}
    return {"items": [{"contact_id": c.id, "email": c.email_normalized, "name": c.name, "status": r.status, "exclusion_reason": r.exclusion_reason,
                       "message_id": msgs[c.id].id if c.id in msgs else None, "message_status": msgs[c.id].status if c.id in msgs else None}
                      for r, c in rows], "total": db.execute(cstmt).scalar_one()}


@router.get("/campaigns/{cid}/messages")
def messages(cid: int, status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
             db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    stmt = select(Message).where(Message.campaign_id == cid)
    cstmt = select(func.count()).select_from(Message).where(Message.campaign_id == cid)
    if status:
        stmt, cstmt = stmt.where(Message.status == status), cstmt.where(Message.status == status)
    rows = db.execute(stmt.order_by(Message.id).limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return {"items": [_msg(m) for m in rows], "total": db.execute(cstmt).scalar_one()}


def _msg(m: Message) -> dict:
    return {
        "id": m.id, "contact_id": m.contact_id, "campaign_id": m.campaign_id, "to": m.recipient_email, "subject": m.subject, "status": m.status,
        "reason": m.status_reason, "dry_run": m.is_dry_run, "step": m.sequence_step, "attempts": m.attempt_count,
        "queued_at": m.queued_at.isoformat(), "not_before": m.not_before.isoformat() if m.not_before else None,
        "submitted_at": m.submitted_at.isoformat() if m.submitted_at else None, "delivered_at": m.delivered_at.isoformat() if m.delivered_at else None,
        "bounced_at": m.bounced_at.isoformat() if m.bounced_at else None, "replied_at": m.replied_at.isoformat() if m.replied_at else None,
        "error_category": m.last_error_category, "provider_message_id": m.provider_message_id,
    }


@router.get("/messages/{mid}")
def message_detail(mid: int, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    m = db.get(Message, mid)
    if m is None:
        raise HTTPException(404, "Message not found")
    attempts = db.execute(select(SendAttempt).where(SendAttempt.message_id == mid).order_by(SendAttempt.attempt_no)).scalars().all()
    events = db.execute(select(OutreachEvent).where(OutreachEvent.message_id == mid).order_by(OutreachEvent.id)).scalars().all()
    return {**_msg(m), "from": m.from_email, "body": m.body_text, "headers": {k: v for k, v in m.extra_headers.items()}, "rfc_message_id": m.rfc_message_id,
            "attempts_detail": [{"no": a.attempt_no, "started_at": a.started_at.isoformat(), "finished_at": a.finished_at.isoformat() if a.finished_at else None,
                                 "outcome": a.outcome, "stage": a.stage, "smtp_code": a.smtp_code, "response": a.response, "category": a.error_category} for a in attempts],
            "events": [{"at": e.occurred_at.isoformat(), "type": e.event_type, "source": e.source, "actor": e.actor, "data": e.data} for e in events]}


class ConfirmBody(BaseModel):
    expected_recipients: int
    phrase: str | None = None


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except svc.CampaignError as e:
        raise HTTPException(409, str(e))


@router.post("/campaigns/{cid}/refresh")
def refresh(cid: int, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    return _ser(db, _wrap(svc.refresh, db, cid, auth.label), True)


@router.post("/campaigns/{cid}/confirm")
def confirm(cid: int, body: ConfirmBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    return _ser(db, _wrap(svc.confirm, db, cid, auth.label, phrase=body.phrase, expected_recipients=body.expected_recipients), True)


class PauseBody(BaseModel):
    reason: str | None = Field(None, max_length=300)


@router.post("/campaigns/{cid}/pause")
def pause(cid: int, body: PauseBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    return _ser(db, _wrap(svc.pause, db, cid, auth.label, body.reason), True)


@router.post("/campaigns/{cid}/resume")
def resume(cid: int, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    return _ser(db, _wrap(svc.resume, db, cid, auth.label), True)


@router.post("/campaigns/{cid}/cancel")
def cancel(cid: int, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    return _ser(db, _wrap(svc.cancel, db, cid, auth.label), True)


class ReconcileBody(BaseModel):
    resolution: Literal["was_sent", "was_not_sent"]
    note: str = Field(min_length=5, max_length=500)


@router.post("/messages/{mid}/reconcile")
def reconcile(mid: int, body: ReconcileBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    try:
        m = engine.reconcile_unknown(db, mid, body.resolution, auth.label, body.note)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return _msg(m)


@router.get("/sending/status")
def sending_status(db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    env = get_settings()
    s = get_group(db, "sending")
    now = datetime.now(timezone.utc)
    tz = limits._tz(get_group(db, "general").timezone)
    today = limits.attempts_since(db, limits.local_day_start(now, tz), False)
    last_hour = limits.attempts_since(db, now - timedelta(hours=1), False)
    provider = get_provider(s.provider)
    return {
        "provider": s.provider, "capabilities": provider.capabilities(), "live_mode_enabled": s.live_mode_enabled,
        "live_enabled_in_env": env.live_sending_enabled, "sent_today": today, "sent_last_hour": last_hour,
        "daily_cap": s.daily_cap, "hourly_cap": s.hourly_cap, "window": {"days": s.window_days, "start": s.window_start, "end": s.window_end},
        "in_window_now": limits.in_window(now, {"days": s.window_days, "start": s.window_start, "end": s.window_end, "tz": get_group(db, "general").timezone}),
        "what_is_confirmable": {
            "submitted": "Confirmed by your mail server accepting the message. It does not prove delivery or that it was read.",
            "delivered": "Only recorded if the receiving system returns a delivery receipt (rare with plain SMTP).",
            "bounced": "Recorded when a bounce / delivery-failure message arrives in the mailbox this app reads (IMAP).",
        },
    }


class TestEmailBody(BaseModel):
    to: str = Field(max_length=320)


@router.post("/sending/test-email")
def test_email(body: TestEmailBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    """Send ONE plain test message to one of YOUR OWN addresses to prove the mailbox works. It can never go to anyone else."""
    from app.core.events import audit
    from app.core.text import normalize_email
    from app.services.drafting import compose
    from app.services.sending.providers import OutgoingMessage

    env = get_settings()
    if not env.smtp_host:
        raise HTTPException(409, "SMTP is not configured in .env")
    sender = get_group(db, "sender")
    own = {a.strip().lower() for a in (sender.from_email, sender.reply_to, env.smtp_from_email, env.smtp_username, env.imap_username) if a and "@" in a}
    to = normalize_email(body.to)
    if not to or to not in own:
        raise HTTPException(400, "Test emails can only be sent to your own addresses: " + (", ".join(sorted(own)) or "(set your From address first)"))
    from_email = compose.sender_from_email(sender, env)
    name = sender.display_name.replace('"', "").strip()
    msg = OutgoingMessage(from_header=f'"{name}" <{from_email}>' if name else from_email, from_email=from_email, to_email=to,
                          subject="Test email from Client Acquisition Studio", rfc_message_id=f"<test-{__import__('uuid').uuid4().hex}@{from_email.rsplit('@', 1)[-1]}>",
                          body="This is a test message sent from your own Client Acquisition Studio.\n\nIf you can read it, your SMTP settings work. "
                               "Check that it arrived in your inbox (not spam) and look at the message source to confirm SPF and DKIM show 'pass'.\n")
    out = SMTPProvider(env).send(msg)
    audit(db, "sending.test_email", actor=auth.label, details={"to": to, "outcome": out.kind})
    return {"ok": out.kind == "accepted", "outcome": out.kind, "stage": out.stage, "response": (out.response or "")[:300], "category": out.error_category,
            "note": "Accepted means your mail server took it. Check your inbox and spam folder." if out.kind == "accepted" else "It was NOT accepted. See the response above."}


@router.post("/sending/test-smtp")
def test_smtp(auth: Auth = Depends(write_access)):
    """Connects and logs in, then disconnects. Sends nothing."""
    env = get_settings()
    if not env.smtp_host:
        raise HTTPException(409, "SMTP_HOST is not configured in .env")
    return SMTPProvider(env).check_connection()
