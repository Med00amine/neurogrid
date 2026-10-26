"""Campaign lifecycle: build -> preview -> explicit confirmation -> run / pause / resume / cancel.

Nothing is queued until `confirm`. Confirmation re-evaluates every recipient, freezes the sender identity
and the exact text of every message, and requires a typed phrase for live campaigns.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.events import audit, record_event
from app.core.settings_store import get_group
from app.models import Campaign, CampaignRecipient, Contact, Draft, DraftVersion, Message, Task
from app.services import contacts as contacts_svc
from app.services import eligibility
from app.services.drafting import compose, service as draft_service
from app.services.sending import limits
from app.services.sending.engine import live_gates

MAX_RECIPIENTS = 1000


class CampaignError(ValueError):
    pass


def required_phrase(c: Campaign, n: int) -> str:
    return f"SEND {n} EMAILS" if c.mode == "live" else f"DRY RUN {n}"


def _window_from_settings(db: Session) -> dict:
    s, g = get_group(db, "sending"), get_group(db, "general")
    return {"days": s.window_days, "start": s.window_start, "end": s.window_end, "tz": g.timezone}


def build(db: Session, *, name: str, mode: str, actor: str, step: int = 1, contact_ids: list[int] | None = None,
          filter: dict | None = None, segment_id: int | None = None, daily_cap: int | None = None, hourly_cap: int | None = None,
          min_interval_seconds: int | None = None, notes: str | None = None) -> Campaign:
    if mode not in ("dry_run", "live"):
        raise CampaignError("mode must be dry_run or live")
    sending = get_group(db, "sending")
    f = contacts_svc.ContactFilter(**{k: v for k, v in (filter or {}).items() if k in contacts_svc.ContactFilter.__dataclass_fields__ and k != "ids"})
    f.segment_id = segment_id or f.segment_id
    ids = list(dict.fromkeys(contact_ids or []))
    if not ids:
        if not (segment_id or filter):
            raise CampaignError("Choose a segment, apply filters or pick contacts. A campaign never targets 'everyone'")
        ids = contacts_svc.resolve_ids(db, f)
    if not ids:
        raise CampaignError("No contacts match")
    if len(ids) > MAX_RECIPIENTS:
        raise CampaignError(f"A campaign can include at most {MAX_RECIPIENTS} contacts; narrow the selection")
    c = Campaign(
        name=name.strip()[:200], mode=mode, status="draft", sequence_step=step, segment_id=segment_id,
        provider="dry_run" if mode == "dry_run" else sending.provider,
        daily_cap=min(daily_cap or sending.daily_cap, sending.daily_cap), hourly_cap=min(hourly_cap or sending.hourly_cap, sending.hourly_cap),
        min_interval_seconds=max(min_interval_seconds or sending.min_interval_seconds, sending.min_interval_seconds),
        window=_window_from_settings(db), notes=notes, created_by=actor,
    )
    db.add(c)
    db.flush()
    _fill_recipients(db, c, ids)
    audit(db, "campaign.create", actor=actor, entity_type="campaign", entity_id=c.id, details={"mode": mode, "step": step, "candidates": len(ids)})
    return c


def _fill_recipients(db: Session, c: Campaign, ids: list[int]) -> None:
    db.execute(text("DELETE FROM campaign_recipients WHERE campaign_id = :c"), {"c": c.id})
    for cid in ids:
        contact = db.get(Contact, cid)
        if contact is None:
            continue
        dv, reason = _draft_for(db, contact, c.sequence_step)
        decision = eligibility.evaluate_contact(db, contact, step=c.sequence_step)
        reasons = [r.text for r in decision.reasons]
        if reason:
            reasons.append(reason)
        db.add(CampaignRecipient(campaign_id=c.id, contact_id=cid, draft_version_id=dv.id if dv else None,
                                 status="excluded" if reasons else "included", exclusion_reason="; ".join(reasons) or None))
    db.flush()
    c.estimate = compute_estimate(db, c)


def _draft_for(db: Session, contact: Contact, step: int) -> tuple[DraftVersion | None, str | None]:
    d = db.execute(select(Draft).where(Draft.contact_id == contact.id, Draft.step == step, Draft.status == "approved")).scalar_one_or_none()
    if d is None:
        return None, "No approved draft for this step"
    if not draft_service.is_approval_valid(db, d):
        return None, "The approval no longer matches the draft text"
    return db.get(DraftVersion, d.approved_version_id), None


def refresh(db: Session, campaign_id: int, actor: str) -> Campaign:
    c = db.get(Campaign, campaign_id)
    if c is None or c.status != "draft":
        raise CampaignError("Only draft campaigns can be refreshed")
    ids = list(db.execute(select(CampaignRecipient.contact_id).where(CampaignRecipient.campaign_id == c.id)).scalars())
    _fill_recipients(db, c, ids)
    return c


def compute_estimate(db: Session, c: Campaign) -> dict:
    inc = db.execute(select(func.count()).select_from(CampaignRecipient).where(CampaignRecipient.campaign_id == c.id, CampaignRecipient.status == "included")).scalar_one()
    exc = db.execute(select(func.count()).select_from(CampaignRecipient).where(CampaignRecipient.campaign_id == c.id, CampaignRecipient.status == "excluded")).scalar_one()
    sending = get_group(db, "sending")
    cap = min(c.daily_cap, sending.daily_cap)
    window = c.window or {}
    open_minutes = 0
    try:
        h1, m1 = map(int, window.get("start", "09:00").split(":"))
        h2, m2 = map(int, window.get("end", "17:00").split(":"))
        open_minutes = (h2 * 60 + m2) - (h1 * 60 + m1)
    except ValueError:
        pass
    per_day_by_interval = max(1, (open_minutes * 60) // max(c.min_interval_seconds, 10)) if open_minutes else cap
    per_day = max(1, min(cap, per_day_by_interval))
    days = math.ceil(inc / per_day) if inc else 0
    send_days = max(1, len(window.get("days", [0, 1, 2, 3, 4])))
    calendar_days = math.ceil(days * 7 / send_days) if days else 0
    draft_cost = db.execute(
        select(func.coalesce(func.sum(DraftVersion.cost_usd_est), 0)).join(CampaignRecipient, CampaignRecipient.draft_version_id == DraftVersion.id)
        .where(CampaignRecipient.campaign_id == c.id, CampaignRecipient.status == "included")
    ).scalar_one()
    return {
        "included": inc, "excluded": exc, "emails_per_day": per_day, "sending_days": days, "calendar_days": calendar_days,
        "sending_cost_usd": 0.0, "sending_cost_note": "Sent through your own mailbox: no per-email charge from this app.",
        "drafting_cost_usd_est": float(draft_cost), "drafting_cost_note": "Estimated cost already spent generating these drafts (price table in Settings).",
        "pacing_note": "Dry runs ignore pacing and finish in seconds. A live run follows the window, caps and minimum interval.",
    }


def confirm(db: Session, campaign_id: int, actor: str, *, phrase: str | None, expected_recipients: int) -> Campaign:
    c = db.execute(select(Campaign).where(Campaign.id == campaign_id).with_for_update()).scalar_one_or_none()
    if c is None:
        raise CampaignError("Campaign not found")
    if c.status != "draft":
        raise CampaignError(f"Campaign is {c.status}; only a draft can be confirmed")
    env = get_settings()
    problems = eligibility.sender_readiness(db, env, live=c.mode == "live")
    if c.mode == "live":
        gate = live_gates(db, c)
        if gate:
            raise CampaignError(f"Live sending is not enabled: {gate}")
    if any(p.code in ("sender_name", "sender_email") for p in problems) or (c.mode == "live" and problems):
        raise CampaignError("Not ready to send: " + "; ".join(p.text for p in problems))

    # re-evaluate everything NOW; the list on screen may be hours old
    ids = list(db.execute(select(CampaignRecipient.contact_id).where(CampaignRecipient.campaign_id == c.id)).scalars())
    _fill_recipients(db, c, ids)
    recipients = db.execute(select(CampaignRecipient).where(CampaignRecipient.campaign_id == c.id, CampaignRecipient.status == "included")).scalars().all()
    n = len(recipients)
    if n == 0:
        raise CampaignError("No recipient is eligible. Fix the exclusions listed on the campaign page first")
    if n != expected_recipients:
        raise CampaignError(f"The recipient list changed: you reviewed {expected_recipients} but {n} are eligible now. Review it again")
    if (phrase or "").strip() != required_phrase(c, n):
        raise CampaignError(f"Type exactly: {required_phrase(c, n)}")

    sender = get_group(db, "sender")
    c.sender_snapshot = {**sender.model_dump(), "from_email": compose.sender_from_email(sender, env)}
    from_email = compose.sender_from_email(sender, env)
    if "@" not in from_email:
        raise CampaignError("The From address is not valid")
    domain = from_email.rsplit("@", 1)[1]
    now = datetime.now(timezone.utc)
    queued = 0
    for r in recipients:
        contact = db.get(Contact, r.contact_id)
        dv = db.get(DraftVersion, r.draft_version_id)
        mid = db.execute(text("SELECT nextval(pg_get_serial_sequence('messages','id'))")).scalar_one()
        rfc = f"<{uuid.uuid4().hex}@{domain}>"
        comp = compose.compose(sender=sender, env=env, subject=dv.subject, core_body=dv.body_text, message_id=mid, contact_id=contact.id,
                               recipient_email=contact.email_normalized, rfc_message_id=rfc)
        key = hashlib.sha256(f"{c.id}|{contact.id}|{c.sequence_step}|{dv.id}".encode()).hexdigest()
        try:
            with db.begin_nested():
                db.add(Message(
                    id=mid, campaign_id=c.id, contact_id=contact.id, draft_version_id=dv.id, sequence_step=c.sequence_step,
                    is_dry_run=c.mode == "dry_run", recipient_email=contact.email_normalized, from_email=comp.from_email, subject=comp.subject,
                    body_text=comp.body, extra_headers={"From": comp.from_header, **comp.headers}, status="queued", provider=c.provider,
                    rfc_message_id=rfc, idempotency_key=key, not_before=now, cost_usd_est=Decimal(dv.cost_usd_est or 0),
                ))
                db.flush()
            queued += 1
            record_event(db, "message_queued", contact_id=contact.id, message_id=mid, campaign_id=c.id, source="user", actor=actor,
                         data={"step": c.sequence_step, "draft_version_id": dv.id, "dry_run": c.mode == "dry_run"})
        except (IntegrityError, DBAPIError) as e:
            # a DB safety rule refused it (live message already exists / suppressed): exclude, never force
            r.status = "excluded"
            r.exclusion_reason = "Refused by a database safety rule: " + str(getattr(e, "orig", e)).split("\n")[0][:200]
    if queued == 0:
        raise CampaignError("Every message was refused by a safety rule; nothing was queued")
    c.status, c.confirmed_by, c.confirmed_at, c.started_at = "running", actor, now, now
    c.estimate = compute_estimate(db, c)
    record_event(db, "campaign_confirmed", campaign_id=c.id, source="user", actor=actor, data={"mode": c.mode, "queued": queued, "phrase": required_phrase(c, n)})
    audit(db, "campaign.confirm", actor=actor, entity_type="campaign", entity_id=c.id, details={"mode": c.mode, "queued": queued})
    db.flush()
    return c


def pause(db: Session, campaign_id: int, actor: str, reason: str | None = None) -> Campaign:
    c = db.execute(select(Campaign).where(Campaign.id == campaign_id).with_for_update()).scalar_one_or_none()
    if c is None or c.status != "running":
        raise CampaignError("Only a running campaign can be paused")
    c.status, c.paused_reason = "paused", reason or "Paused by operator"
    record_event(db, "campaign_paused", campaign_id=c.id, source="user", actor=actor, data={"reason": c.paused_reason})
    audit(db, "campaign.pause", actor=actor, entity_type="campaign", entity_id=c.id)
    return c


def resume(db: Session, campaign_id: int, actor: str) -> Campaign:
    c = db.execute(select(Campaign).where(Campaign.id == campaign_id).with_for_update()).scalar_one_or_none()
    if c is None or c.status != "paused":
        raise CampaignError("Only a paused campaign can be resumed")
    unknown = db.execute(select(func.count()).select_from(Message).where(Message.campaign_id == c.id, Message.status == "unknown")).scalar_one()
    if unknown:
        raise CampaignError(f"{unknown} message(s) have an unknown outcome. Resolve them (Campaigns -> queue) before resuming")
    if c.mode == "live":
        gate = live_gates(db, c)
        if gate:
            raise CampaignError(f"Cannot resume: {gate}")
        problems = eligibility.sender_readiness(db, get_settings(), live=True)
        if problems:
            raise CampaignError("Cannot resume: " + "; ".join(p.text for p in problems))
    c.status, c.paused_reason, c.backoff_until, c.consecutive_failures = "running", None, None, 0
    record_event(db, "campaign_resumed", campaign_id=c.id, source="user", actor=actor)
    audit(db, "campaign.resume", actor=actor, entity_type="campaign", entity_id=c.id)
    return c


def cancel(db: Session, campaign_id: int, actor: str) -> Campaign:
    c = db.execute(select(Campaign).where(Campaign.id == campaign_id).with_for_update()).scalar_one_or_none()
    if c is None or c.status in ("cancelled", "completed"):
        raise CampaignError("Campaign is already finished")
    n = db.execute(
        update(Message).where(Message.campaign_id == c.id, Message.status == "queued")
        .values(status="cancelled", status_reason=f"Campaign cancelled by {actor}").returning(Message.id)
    ).all()
    db.execute(update(Task).where(Task.status == "open", Task.kind == "followup", Task.source_message_id.in_(select(Message.id).where(Message.campaign_id == c.id)))
               .values(status="cancelled", cancel_reason="campaign cancelled"))
    c.status, c.finished_at = "cancelled", datetime.now(timezone.utc)
    record_event(db, "campaign_cancelled", campaign_id=c.id, source="user", actor=actor, data={"cancelled_messages": len(n)})
    audit(db, "campaign.cancel", actor=actor, entity_type="campaign", entity_id=c.id, details={"cancelled_messages": len(n)})
    return c
