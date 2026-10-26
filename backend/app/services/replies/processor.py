"""Turn parsed inbound mail into state changes.

Matching order (never by display name): In-Reply-To -> References -> bounce's embedded original Message-ID ->
bounce's Final-Recipient -> (weak) the sender's exact address + same subject. Weak matches are flagged.
Stop rules: any real reply, unsubscribe, hard bounce or complaint cancels scheduled follow-ups immediately.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.events import audit, record_event
from app.core.settings_store import get_group
from app.models import Contact, InboundMessage, MailboxCursor, Message, Task
from app.services import llm as llm_mod
from app.services import suppression
from app.services.replies import classify as cls_mod
from app.services.replies.imap import MailboxClient
from app.services.replies.parse import DSN, Parsed, parse_message

log = logging.getLogger("replies")

LIVE_STATUSES = ("submitted", "delivered", "replied", "unknown", "bounced")
STAGES_BEFORE_REPLY = ("new", "researched", "qualified", "draft_ready", "approved", "contacted")


def _norm_subject(s: str | None) -> str:
    return re.sub(r"^\s*((re|aw|sv|fwd?|wg|tr)\s*:\s*)+", "", (s or ""), flags=re.I).strip().lower()


def match_message(db: Session, p: Parsed, kind: str) -> tuple[Message | None, str | None]:
    ids: list[tuple[str, str]] = []
    if p.in_reply_to:
        ids.append((p.in_reply_to, "in_reply_to"))
    ids += [(r, "references") for r in reversed(p.references)]
    if p.dsn and p.dsn.original_message_id:
        ids.append((p.dsn.original_message_id, "dsn_original_message_id"))
    for mid, method in ids:
        m = db.execute(select(Message).where(Message.rfc_message_id == mid, Message.is_dry_run.is_(False))).scalar_one_or_none()
        if m is not None:
            return m, method
    now = datetime.now(timezone.utc)
    if p.dsn and p.dsn.final_recipient:
        cands = db.execute(
            select(Message).where(Message.recipient_email == p.dsn.final_recipient, Message.is_dry_run.is_(False), Message.status.in_(LIVE_STATUSES),
                                  Message.submitted_at >= now - timedelta(days=30)).order_by(Message.id.desc()).limit(2)
        ).scalars().all()
        if len(cands) == 1:
            return cands[0], "dsn_final_recipient (weak)"
    if kind != "bounce" and p.from_email:
        cands = db.execute(
            select(Message).where(Message.recipient_email == p.from_email, Message.is_dry_run.is_(False), Message.status.in_(LIVE_STATUSES))
            .order_by(Message.id.desc()).limit(5)
        ).scalars().all()
        same = [m for m in cands if _norm_subject(m.subject) == _norm_subject(p.subject)]
        if len(same) == 1:
            return same[0], "sender_address_and_subject (weak)"
        if len(cands) >= 1 and cls_mod.UNSUB.search((p.subject or "") + " " + p.body[:500]):
            return cands[0], "sender_address (weak, opt-out request)"
    return None, None


def stop_followups(db: Session, contact_id: int, reason: str) -> int:
    n = db.execute(
        update(Task).where(Task.contact_id == contact_id, Task.status == "open", Task.kind == "followup")
        .values(status="cancelled", cancel_reason=reason).returning(Task.id)
    ).all()
    if n:
        record_event(db, "followups_stopped", contact_id=contact_id, source="system", data={"reason": reason, "tasks": len(n)})
    return len(n)


def _set_stage(db: Session, c: Contact, stage: str, why: str, actor: str = "system") -> None:
    if c.pipeline_stage != stage:
        record_event(db, "stage_changed", contact_id=c.id, source="system" if actor == "system" else "user", actor=actor,
                     data={"from": c.pipeline_stage, "to": stage, "why": why})
        c.pipeline_stage = stage


def _is_hard_bounce(d: DSN) -> bool:
    st = d.status or ""
    if d.action == "failed" and not st.startswith("4"):
        return not st.startswith("5.2.2")  # 5.2.2 = mailbox full: treat as soft
    return st.startswith("5") and not st.startswith("5.2.2")


def process_parsed(db: Session, p: Parsed, *, mailbox: str, uidvalidity: int, uid: int, llm_client=None) -> InboundMessage | None:
    env = get_settings()
    if p.message_id:
        if db.execute(select(InboundMessage.id).where(InboundMessage.rfc_message_id == p.message_id)).first():
            return None  # already processed
        if db.execute(select(Message.id).where(Message.rfc_message_id == p.message_id)).first():
            return None  # our own sent copy
    ours = {x.lower() for x in (env.smtp_from_email, get_group(db, "sender").from_email) if x}
    kind = "bounce" if p.dsn else ("auto_reply" if p.is_auto else "reply")
    if kind == "reply" and p.from_email in ours:
        return None  # a message from ourselves (e.g. a Sent copy in the inbox), not a reply

    msg, method = match_message(db, p, kind)
    contact = db.get(Contact, msg.contact_id) if msg else None
    if contact is None and p.from_email and kind != "bounce":
        contact = db.execute(select(Contact).where(Contact.email_normalized == p.from_email)).scalar_one_or_none()

    c = cls_mod.classify(p) if kind != "bounce" else None
    if kind == "reply" and c and c.label == "uncertain" and llm_client is not None and get_group(db, "llm").classify_replies:
        try:
            c = cls_mod.classify_with_llm(db, llm_client, p, model=get_group(db, "llm").research_model, contact_id=contact.id if contact else None)
        except llm_mod.LLMError as e:
            log.warning("AI classification skipped: %s", e)

    row = InboundMessage(
        mailbox=mailbox, uidvalidity=uidvalidity, imap_uid=uid, rfc_message_id=p.message_id, in_reply_to=p.in_reply_to,
        references_header=" ".join(p.references)[:4000] or None, from_email=p.from_email, from_name=p.from_name, subject=p.subject,
        received_at=p.received_at, body_text=p.body[:30000], kind=kind, matched_message_id=msg.id if msg else None,
        matched_contact_id=contact.id if contact else None, match_method=method,
        classification=c.label if c else None, classification_source=c.source if c else None,
        classification_confidence=c.confidence if c else None, classification_reasons=c.reasons if c else [],
        dsn={"action": p.dsn.action, "status": p.dsn.status, "final_recipient": p.dsn.final_recipient, "diagnostic": p.dsn.diagnostic,
             "original_message_id": p.dsn.original_message_id} if p.dsn else {},
    )
    db.add(row)
    db.flush()
    now = datetime.now(timezone.utc)

    if kind == "bounce":
        _apply_dsn(db, row, p.dsn, msg, contact, now)
    elif kind == "auto_reply":
        record_event(db, "auto_response_received", contact_id=contact.id if contact else None, message_id=msg.id if msg else None,
                     campaign_id=msg.campaign_id if msg else None, source="imap", data={"inbound_id": row.id})
    else:
        record_event(db, "reply_received", contact_id=contact.id if contact else None, message_id=msg.id if msg else None,
                     campaign_id=msg.campaign_id if msg else None, source="imap",
                     data={"inbound_id": row.id, "classification": row.classification, "match": method})
        if msg is not None and msg.status in ("submitted", "delivered", "unknown"):
            msg.status, msg.replied_at = "replied", now
        if contact is not None:
            stop_followups(db, contact.id, "reply received")
            if contact.pipeline_stage in STAGES_BEFORE_REPLY:
                _set_stage(db, contact, "replied", "reply received")
        if c and c.explicit_opt_out and p.from_email:
            target = contact.email_normalized if contact else p.from_email
            suppression.suppress(db, value=target, reason="unsubscribe", source=f"reply #{row.id}: {c.reasons[0] if c.reasons else ''}"[:300],
                                 contact_id=contact.id if contact else None, message_id=msg.id if msg else None, actor="system")
            if p.from_email != target:
                suppression.suppress(db, value=p.from_email, reason="unsubscribe", source=f"reply #{row.id}", actor="system")
            record_event(db, "unsubscribed", contact_id=contact.id if contact else None, message_id=msg.id if msg else None,
                         source="imap", data={"via": "reply", "inbound_id": row.id})
        elif contact is not None and (c is None or c.label in ("interested", "info_request", "referral", "uncertain")):
            db.add(Task(contact_id=contact.id, kind="review", title=f"Reply to {contact.name or contact.email_normalized} ({c.label if c else 'reply'})",
                        status="open", auto=True, due_at=now, source_message_id=msg.id if msg else None))
    db.flush()
    return row


def _apply_dsn(db: Session, row: InboundMessage, d: DSN, msg: Message | None, contact: Contact | None, now: datetime) -> None:
    kind_ev = None
    if d.action == "delivered":
        if msg is not None and msg.status == "submitted":
            msg.status, msg.delivered_at = "delivered", now
        kind_ev = "delivery_receipt"
    elif _is_hard_bounce(d):
        if msg is not None and msg.status in ("submitted", "delivered", "unknown", "replied"):
            msg.status, msg.bounced_at = "bounced", now
            msg.status_reason = f"Bounced: {d.status or ''} {d.diagnostic or ''}"[:300]
        email_addr = (msg.recipient_email if msg else None) or d.final_recipient
        if email_addr:
            suppression.suppress(db, value=email_addr, reason="bounce_hard", source=f"bounce notification #{row.id} ({d.status})",
                                 contact_id=contact.id if contact else None, message_id=msg.id if msg else None, actor="system")
        if contact is not None:
            stop_followups(db, contact.id, "hard bounce")
        kind_ev = "bounce_received"
    else:
        kind_ev = "delivery_delayed" if d.action in ("delayed", None) else "dsn_received"
    record_event(db, kind_ev, contact_id=contact.id if contact else None, message_id=msg.id if msg else None,
                 campaign_id=msg.campaign_id if msg else None, source="provider",
                 data={"inbound_id": row.id, "action": d.action, "status": d.status, "match": row.match_method})


# --- polling -----------------------------------------------------------------------------------------------------------------------


def poll(client: MailboxClient, mailbox: str, session_factory, *, limit: int = 100, llm_client=None) -> dict:
    """Fetch new mail since the stored cursor and process it, one transaction per message. Safe to repeat."""
    with session_factory() as db:
        cur = db.get(MailboxCursor, mailbox)
        last_uid, known = (cur.last_uid, cur.uidvalidity) if cur else (0, None)
    batch = client.fetch_new(mailbox, last_uid, known, limit)
    stats = {"fetched": len(batch.items), "processed": 0, "replies": 0, "bounces": 0, "auto": 0, "skipped": 0, "errors": 0, "has_more": batch.has_more}
    max_uid = last_uid if (known is None or known == batch.uidvalidity) else 0
    for uid, raw in batch.items:
        try:
            parsed = parse_message(raw)
            with session_factory() as db:
                row = process_parsed(db, parsed, mailbox=mailbox, uidvalidity=batch.uidvalidity, uid=uid, llm_client=llm_client)
                if row is None:
                    stats["skipped"] += 1
                else:
                    stats["processed"] += 1
                    stats[{"reply": "replies", "bounce": "bounces", "auto_reply": "auto"}.get(row.kind, "skipped")] += 1
            max_uid = max(max_uid, uid)
        except Exception:  # noqa: BLE001 - one malformed message must never block the mailbox
            log.exception("could not process inbound uid %s", uid)
            stats["errors"] += 1
            max_uid = max(max_uid, uid)  # do not retry a poison message forever; it is logged
    with session_factory() as db:
        cur = db.get(MailboxCursor, mailbox)
        if cur is None:
            db.add(MailboxCursor(mailbox=mailbox, uidvalidity=batch.uidvalidity, last_uid=max_uid))
        else:
            cur.uidvalidity, cur.last_uid = batch.uidvalidity, max_uid
    return stats


# --- operator corrections ------------------------------------------------------------------------------------------------------------


def apply_user_classification(db: Session, inbound_id: int, label: str, actor: str, *, mark_lost: bool = False) -> InboundMessage:
    from app.constants import REPLY_CLASSES

    if label not in REPLY_CLASSES:
        raise ValueError("unknown classification")
    row = db.get(InboundMessage, inbound_id)
    if row is None:
        raise ValueError("reply not found")
    before = row.classification
    row.classification, row.classification_source, row.classification_confidence = label, "user", 1.0
    row.reviewed, row.reviewed_by = True, actor
    contact = db.get(Contact, row.matched_contact_id) if row.matched_contact_id else None
    record_event(db, "reply_classified", contact_id=contact.id if contact else None, message_id=row.matched_message_id, source="user", actor=actor,
                 data={"inbound_id": row.id, "from": before, "to": label})
    if label == "unsubscribe" and row.from_email:
        suppression.suppress(db, value=(contact.email_normalized if contact else row.from_email), reason="unsubscribe",
                             source=f"reply #{row.id} (classified by {actor})", contact_id=contact.id if contact else None, actor=actor)
    if contact is not None:
        if label == "interested" and contact.pipeline_stage in STAGES_BEFORE_REPLY + ("replied",):
            _set_stage(db, contact, "interested", "reply classified as interested", actor)
        if label == "not_interested" and mark_lost and contact.pipeline_stage not in ("won", "do_not_contact"):
            _set_stage(db, contact, "lost", "reply classified as not interested", actor)
            stop_followups(db, contact.id, "not interested")
        if label in ("not_interested", "referral"):
            stop_followups(db, contact.id, f"reply classified as {label}")
    audit(db, "reply.classify", actor=actor, entity_type="inbound_message", entity_id=row.id, details={"from": before, "to": label})
    db.flush()
    return row
