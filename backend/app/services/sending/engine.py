"""The send loop. One `tick()` = recover stuck sends, then try to send at most a few messages.

The order of operations for every message is what prevents duplicates:

  1. (transaction, advisory-locked)  re-check everything -> mark `submitting` -> write attempt row -> COMMIT
  2. call the provider (no database transaction open)
  3. (transaction)                   record the outcome

If the process dies between 1 and 3 the message stays `submitting`; recovery turns it into `unknown`.
It is NEVER put back into the queue automatically, because we cannot know whether the mail left.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.events import audit, record_event
from app.core.settings_store import get_group
from app.db import session_scope
from app.jobs.periodic import periodic
from app.models import Campaign, Contact, Draft, Message, SendAttempt, Task
from app.services import eligibility, suppression
from app.services.sending import limits
from app.services.sending.providers import OutgoingMessage, SendOutcome, get_provider

log = logging.getLogger("sending")

BOUNCE_RATE_PAUSE = 0.08  # our own safety threshold (not a provider rule): pause above 8% hard failures
BOUNCE_RATE_MIN_SAMPLE = 20
MAX_PER_TICK = 25


def _now() -> datetime:
    return datetime.now(timezone.utc)


def pause_campaign(db: Session, campaign: Campaign, reason: str, actor: str = "system") -> None:
    if campaign.status not in ("running",):
        return
    campaign.status = "paused"
    campaign.paused_reason = reason
    record_event(db, "campaign_paused", campaign_id=campaign.id, source="system" if actor == "system" else "user", actor=actor, data={"reason": reason})
    log.warning("campaign %s paused: %s", campaign.id, reason)


def live_gates(db: Session, campaign: Campaign) -> str | None:
    """Reasons a LIVE campaign may not send right now (None = fine)."""
    env = get_settings()
    sending = get_group(db, "sending")
    if not env.live_sending_enabled:
        return "LIVE_SENDING_ENABLED is not true in the server environment"
    if not sending.live_mode_enabled:
        return "Live mode is switched off in Settings"
    if sending.provider != "smtp" or campaign.provider != "smtp":
        return "The sending provider is not SMTP"
    return None


# --- recovery ------------------------------------------------------------------------------------------------------------------


def recover_stuck(db: Session, now: datetime | None = None) -> int:
    now = now or _now()
    sending = get_group(db, "sending")
    env = get_settings()
    cutoff = now - timedelta(seconds=max(sending.attempt_timeout_seconds, 3 * env.smtp_timeout_seconds) + 60)
    stuck = db.execute(
        select(Message).where(Message.status == "submitting", Message.submission_started_at < cutoff).with_for_update(skip_locked=True)
    ).scalars().all()
    for m in stuck:
        m.status = "unknown"
        m.status_reason = "The send was interrupted before the provider's answer was recorded. It may or may not have been sent."
        m.last_error_category = "ambiguous"
        record_event(db, "message_unknown", contact_id=m.contact_id, message_id=m.id, campaign_id=m.campaign_id, source="system",
                     data={"why": "interrupted_submission"})
        db.add(Task(contact_id=m.contact_id, title=f"Investigate message #{m.id}: outcome unknown", kind="investigate", status="open", auto=True,
                    source_message_id=m.id, due_at=now))
        c = db.get(Campaign, m.campaign_id)
        if c:
            pause_campaign(db, c, f"Message #{m.id} has an unknown outcome. Check your Sent folder, resolve it, then resume.")
    return len(stuck)


# --- one send --------------------------------------------------------------------------------------------------------------------


def _claim(db: Session, campaign_id: int, now: datetime) -> tuple[str, int | None, dict | None]:
    """Phase 1. Returns (state, message_id, info). state: sent_started | none | waiting | blocked"""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('neurogrid:send'))"))
    c = db.execute(select(Campaign).where(Campaign.id == campaign_id).with_for_update()).scalar_one_or_none()
    if c is None or c.status != "running":
        return "none", None, None
    if c.mode == "live":
        problem = live_gates(db, c)
        if problem:
            pause_campaign(db, c, problem)
            return "blocked", None, {"reason": problem}
    gate = limits.send_gate(db, c, now)
    if not gate.allowed:
        return "waiting", None, {"reason": gate.reason, "retry_at": gate.retry_at.isoformat() if gate.retry_at else None}
    msg = db.execute(
        select(Message).where(Message.campaign_id == c.id, Message.status == "queued", Message.not_before <= now)
        .order_by(Message.id).with_for_update(skip_locked=True).limit(1)
    ).scalar_one_or_none()
    if msg is None:
        return "none", None, None

    contact = db.get(Contact, msg.contact_id)
    decision = eligibility.evaluate_contact(db, contact, step=msg.sequence_step, now=now, ignore_message_id=msg.id)
    draft_ok = True
    if msg.draft_version_id:
        approved_ids = db.execute(select(Draft.approved_version_id).where(Draft.contact_id == msg.contact_id, Draft.step == msg.sequence_step,
                                                                          Draft.status == "approved")).scalars().all()
        draft_ok = msg.draft_version_id in approved_ids
    if not decision.ok or not draft_ok:
        suppressed = "suppressed" in decision.codes
        msg.status = "suppressed" if suppressed else "cancelled"
        why = decision.summary() if not decision.ok else "The approved draft was withdrawn or changed after the campaign was confirmed"
        msg.status_reason = f"Blocked at send time: {why}"
        record_event(db, "send_blocked", contact_id=msg.contact_id, message_id=msg.id, campaign_id=c.id, source="system",
                     data={"reasons": decision.codes if not decision.ok else ["approval_withdrawn"], "text": why})
        return "blocked", msg.id, {"reasons": decision.codes}

    attempt_no = msg.attempt_count + 1
    msg.status = "submitting"  # the database re-checks suppression here (trigger)
    msg.attempt_count = attempt_no
    msg.submission_started_at = now
    db.add(SendAttempt(message_id=msg.id, attempt_no=attempt_no, started_at=now, stage="started"))
    record_event(db, "submission_started", contact_id=msg.contact_id, message_id=msg.id, campaign_id=c.id, source="system",
                 data={"attempt": attempt_no, "provider": msg.provider, "precheck": "passed", "dry_run": msg.is_dry_run})
    db.flush()
    return "sent_started", msg.id, {"attempt": attempt_no}


def _apply(db: Session, message_id: int, out: SendOutcome, now: datetime) -> None:
    m = db.execute(select(Message).where(Message.id == message_id).with_for_update()).scalar_one()
    c = db.execute(select(Campaign).where(Campaign.id == m.campaign_id).with_for_update()).scalar_one()
    sending = get_group(db, "sending")
    attempt = db.execute(select(SendAttempt).where(SendAttempt.message_id == m.id, SendAttempt.attempt_no == m.attempt_count)).scalar_one()
    attempt.finished_at, attempt.outcome, attempt.stage = now, out.kind, out.stage
    attempt.smtp_code, attempt.response, attempt.error_category = out.smtp_code, (out.response or "")[:2000], out.error_category
    m.last_error_category, m.last_error = out.error_category, (out.response or None)

    if out.kind == "accepted":
        m.status, m.submitted_at, m.provider_message_id, m.status_reason = "submitted", now, out.provider_message_id, None
        c.consecutive_failures = 0
        record_event(db, "message_submitted", contact_id=m.contact_id, message_id=m.id, campaign_id=c.id, source="provider",
                     data={"provider": m.provider, "provider_message_id": out.provider_message_id, "dry_run": m.is_dry_run,
                           "note": "accepted by the mail server; this is not proof of delivery", "dsn_requested": out.dsn_requested})
        if not m.is_dry_run:
            _after_live_submit(db, m, sending, now)
    elif out.kind == "rejected":
        m.status, m.status_reason = "rejected", f"Refused by the mail system ({out.error_category}): {(out.response or '')[:300]}"
        record_event(db, "message_rejected", contact_id=m.contact_id, message_id=m.id, campaign_id=c.id, source="provider",
                     data={"category": out.error_category, "code": out.smtp_code, "response": (out.response or "")[:300], "stage": out.stage})
        if out.error_category == "mailbox_unavailable" and not m.is_dry_run:
            suppression.suppress(db, value=m.recipient_email, reason="bounce_hard", source=f"rejected at send time (message {m.id})",
                                 contact_id=m.contact_id, message_id=m.id, actor="system")
        if out.error_category in ("policy_rejected", "content_rejected"):
            c.consecutive_failures += 1
        if c.consecutive_failures >= sending.max_consecutive_temp_failures:
            pause_campaign(db, c, "Recipient mail servers keep rejecting these messages for policy/content reasons. Stop and check your sender "
                                  "reputation (SPF, DKIM, DMARC) before continuing.")
    elif out.kind in ("temporary_failure", "pre_send_failure", "auth_failure"):
        # provably NOT accepted -> safe to retry later, with backoff
        c.consecutive_failures += 1
        if m.attempt_count >= sending.max_attempts_per_message and out.kind != "auth_failure":
            m.status, m.status_reason = "failed", f"Gave up after {m.attempt_count} attempts; the message was never accepted by the server ({out.error_category})"
            record_event(db, "message_failed", contact_id=m.contact_id, message_id=m.id, campaign_id=c.id, source="system",
                         data={"category": out.error_category, "attempts": m.attempt_count, "response": (out.response or "")[:300]})
        else:
            delay = limits.retry_delay(m.attempt_count)
            if out.error_category == "rate_limited":
                delay = max(delay, timedelta(hours=1))
                c.backoff_until = now + delay
            m.status, m.not_before = "queued", now + delay
            m.status_reason = f"Will retry after {delay} ({out.error_category})"
            record_event(db, "message_requeued", contact_id=m.contact_id, message_id=m.id, campaign_id=c.id, source="system",
                         data={"category": out.error_category, "retry_in_seconds": int(delay.total_seconds()), "attempt": m.attempt_count})
        if out.kind == "auth_failure":
            pause_campaign(db, c, "The mail server rejected the login. Fix SMTP_USERNAME/SMTP_PASSWORD in .env, restart, then resume.")
        elif c.consecutive_failures >= sending.max_consecutive_temp_failures:
            pause_campaign(db, c, f"{c.consecutive_failures} provider errors in a row (last: {out.error_category}). Check the mail server, then resume.")
    else:  # unknown
        m.status = "unknown"
        m.status_reason = f"The connection broke after the message was transmitted ({(out.response or '')[:200]}). It may have been sent. NOT retried automatically."
        record_event(db, "message_unknown", contact_id=m.contact_id, message_id=m.id, campaign_id=c.id, source="system",
                     data={"stage": out.stage, "response": (out.response or "")[:300]})
        db.add(Task(contact_id=m.contact_id, title=f"Investigate message #{m.id}: outcome unknown", kind="investigate", status="open", auto=True,
                    source_message_id=m.id, due_at=now))
        pause_campaign(db, c, f"Message #{m.id} has an unknown outcome. Check your Sent folder, resolve it, then resume.")
    db.flush()  # the queries below must see this message's new status
    _bounce_rate_guard(db, c, sending)
    _maybe_complete(db, c, now)


def _bounce_rate_guard(db: Session, c: Campaign, sending) -> None:
    if c.mode != "live" or c.status != "running":
        return
    rows = dict(db.execute(select(Message.status, func.count()).where(Message.campaign_id == c.id, Message.status.in_(
        ("submitted", "delivered", "bounced", "replied", "rejected")), Message.last_error_category.is_distinct_from("policy_rejected")).group_by(Message.status)).all())
    total = sum(rows.values())
    bad = rows.get("bounced", 0) + rows.get("rejected", 0)
    if total >= BOUNCE_RATE_MIN_SAMPLE and bad / total >= BOUNCE_RATE_PAUSE:
        pause_campaign(db, c, f"{bad} of {total} messages bounced or were refused ({bad / total:.0%}). That is above this app's safety limit "
                              f"of {BOUNCE_RATE_PAUSE:.0%}; clean the list before continuing.")


def _after_live_submit(db: Session, m: Message, sending, now: datetime) -> None:
    contact = db.get(Contact, m.contact_id)
    if contact.history_status != "previously_contacted":
        record_event(db, "history_updated", contact_id=contact.id, source="system", data={"from": contact.history_status, "to": "previously_contacted", "message_id": m.id})
        contact.history_status = "previously_contacted"
    if contact.pipeline_stage in ("new", "researched", "qualified", "draft_ready", "approved"):
        record_event(db, "stage_changed", contact_id=contact.id, source="system", data={"from": contact.pipeline_stage, "to": "contacted", "message_id": m.id})
        contact.pipeline_stage = "contacted"
    if m.sequence_step <= sending.max_followups:  # step 1 + max_followups total emails
        db.add(Task(contact_id=contact.id, title=f"Follow-up {m.sequence_step} (only if there is no reply)", kind="followup", status="open", auto=True,
                    source_message_id=m.id, followup_step=m.sequence_step + 1, due_at=now + timedelta(days=sending.followup_gap_days)))


def _maybe_complete(db: Session, c: Campaign, now: datetime) -> None:
    if c.status != "running":
        return
    open_n = db.execute(select(func.count()).select_from(Message).where(Message.campaign_id == c.id, Message.status.in_(("queued", "submitting")))).scalar_one()
    if open_n == 0:
        c.status, c.finished_at = "completed", now
        record_event(db, "campaign_completed", campaign_id=c.id, source="system")


def _send_one(campaign_id: int, now: datetime) -> str:
    out_msg: OutgoingMessage | None = None
    provider_name = "dry_run"
    mid: int | None = None
    with session_scope() as db:
        state, mid, info = _claim(db, campaign_id, now)
        if state == "sent_started":
            m = db.get(Message, mid)
            out_msg = OutgoingMessage(from_header=m.extra_headers.get("From") or m.from_email, from_email=m.from_email, to_email=m.recipient_email,
                                      subject=m.subject, body=m.body_text, rfc_message_id=m.rfc_message_id,
                                      headers={k: v for k, v in m.extra_headers.items() if k != "From"})
            provider_name = m.provider
        elif state == "none":
            camp = db.get(Campaign, campaign_id)
            if camp is not None:
                _maybe_complete(db, camp, now)
    if state != "sent_started" or out_msg is None or mid is None:
        return state
    # ---- phase 2: no transaction is open while we talk to the provider ----
    try:
        outcome = get_provider(provider_name).send(out_msg)
    except Exception as e:  # noqa: BLE001 - an unexpected exception inside send() leaves the outcome unknown
        log.exception("provider raised for message %s", mid)
        outcome = SendOutcome("unknown", "after_data", None, f"{type(e).__name__}: {str(e)[:200]}", error_category="ambiguous", retry_safe=False)
    # ---- phase 3 ----
    with session_scope() as db:
        _apply(db, mid, outcome, now)
    return "sent"


def tick(now: datetime | None = None) -> dict:
    """Process running campaigns. Safe to call from several processes; the advisory lock serialises decisions."""
    now = now or _now()
    stats = {"sent": 0, "blocked": 0, "waiting": 0, "recovered": 0}
    with session_scope() as db:
        stats["recovered"] = recover_stuck(db, now)
        ids = list(db.execute(select(Campaign.id).where(Campaign.status == "running").order_by(Campaign.id)).scalars())
    for cid in ids:
        for _ in range(MAX_PER_TICK):
            state = _send_one(cid, now)
            if state == "sent":
                stats["sent"] += 1
            elif state == "blocked":
                stats["blocked"] += 1
            else:
                if state == "waiting":
                    stats["waiting"] += 1
                break
    return stats


@periodic("send_tick", 5)
def _periodic_tick() -> None:
    tick()


# --- operator reconciliation of unknown outcomes -----------------------------------------------------------------------------------


def reconcile_unknown(db: Session, message_id: int, resolution: str, actor: str, note: str) -> Message:
    """The operator checked the real Sent folder / recipient and tells us what happened."""
    if resolution not in ("was_sent", "was_not_sent"):
        raise ValueError("resolution must be was_sent or was_not_sent")
    if len(note.strip()) < 5:
        raise ValueError("Add a short note on how you verified it")
    m = db.execute(select(Message).where(Message.id == message_id).with_for_update()).scalar_one_or_none()
    if m is None or m.status != "unknown":
        raise ValueError("Only messages with an unknown outcome can be reconciled")
    now = _now()
    if resolution == "was_sent":
        m.status, m.submitted_at, m.status_reason = "submitted", now, f"Operator confirmed it was sent: {note.strip()}"
        if not m.is_dry_run:
            _after_live_submit(db, m, get_group(db, "sending"), now)
    else:
        # it never left: mark failed. The unique index then allows a deliberate new message later.
        m.status, m.status_reason = "failed", f"Operator confirmed it was NOT sent: {note.strip()}"
    record_event(db, "message_reconciled", contact_id=m.contact_id, message_id=m.id, campaign_id=m.campaign_id, source="user", actor=actor,
                 data={"resolution": resolution, "note": note.strip()})
    audit(db, "message.reconcile", actor=actor, entity_type="message", entity_id=m.id, details={"resolution": resolution})
    db.flush()
    db.execute(text("UPDATE tasks SET status='done', completed_at=now() WHERE source_message_id=:m AND kind='investigate' AND status='open'"), {"m": m.id})
    return m
