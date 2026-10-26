"""Global suppression list.

Rules (see docs/02): imports can only ADD suppressions; rows are never deleted; a suppression is
only lifted by an explicit, audited, typed-confirmation action; unsubscribes / complaints / legal
holds need an additional force flag. Adding a suppression immediately cancels queued messages and
open follow-ups for that recipient.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.constants import SUPPRESSION_REASONS
from app.core.events import audit, record_event
from app.core.text import normalize_email
from app.models import Contact, Message, Suppression, Task

# Reasons that signal the person does not want to hear from us -> never lift without force.
PROTECTED_REASONS = {"unsubscribe", "complaint", "legal", "replied_stop", "do_not_contact"}
_DNC_STAGE_REASONS = {"unsubscribe", "complaint", "legal", "replied_stop", "do_not_contact", "manual"}


class SuppressionError(ValueError):
    pass


def normalize_value(kind: str, value: str) -> str:
    if kind == "email":
        v = normalize_email(value)
        if not v:
            raise SuppressionError(f"not a valid email address: {value!r}")
        return v
    if kind == "domain":
        v = value.strip().lower().lstrip("@")
        if not v or "." not in v or " " in v or "/" in v:
            raise SuppressionError(f"not a valid domain: {value!r}")
        return v
    raise SuppressionError(f"unknown suppression kind {kind!r}")


def find_active(db: Session, email: str) -> Suppression | None:
    """Return the active suppression (email or domain) covering this address, if any."""
    email = email.lower()
    domain = email.rsplit("@", 1)[-1]
    stmt = (
        select(Suppression)
        .where(
            Suppression.active.is_(True),
            or_(
                and_(Suppression.kind == "email", Suppression.value == email),
                and_(Suppression.kind == "domain", Suppression.value == domain),
            ),
        )
        .order_by(Suppression.kind)  # email match first
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def suppress(
    db: Session,
    *,
    kind: str = "email",
    value: str,
    reason: str,
    source: str | None = None,
    contact_id: int | None = None,
    message_id: int | None = None,
    actor: str | None = None,
    notes: str | None = None,
    cascade: bool = True,
) -> Suppression:
    if reason not in SUPPRESSION_REASONS:
        raise SuppressionError(f"unknown reason {reason!r}")
    value = normalize_value(kind, value)
    row = db.execute(select(Suppression).where(Suppression.kind == kind, Suppression.value == value)).scalar_one_or_none()
    if row is None:
        row = Suppression(
            kind=kind, value=value, reason=reason, source=source, contact_id=contact_id,
            message_id=message_id, created_by=actor, notes=notes, active=True,
        )
        db.add(row)
    elif not row.active:
        # re-suppressing a lifted entry: reactivate, keep history in audit/events
        row.active = True
        row.reason = reason
        row.source = source or row.source
        row.deactivated_at = None
        row.deactivated_by = None
        row.deactivate_reason = None
    # already active: keep the original (never downgrade the recorded reason)
    db.flush()
    audit(db, "suppression.add", actor=actor, entity_type="suppression", entity_id=row.id,
          details={"kind": kind, "value": value, "reason": reason, "source": source})
    if cascade:
        _cascade(db, row, actor)
    return row


def _contacts_for(db: Session, s: Suppression):
    if s.kind == "email":
        return db.execute(select(Contact).where(Contact.email_normalized == s.value)).scalars().all()
    return db.execute(select(Contact).where(Contact.email_domain == s.value)).scalars().all()


def _cascade(db: Session, s: Suppression, actor: str | None) -> None:
    contacts = _contacts_for(db, s)
    ids = [c.id for c in contacts]
    if not ids:
        return
    now = datetime.now(timezone.utc)
    # queued messages never go out
    cancelled = db.execute(
        update(Message)
        .where(Message.contact_id.in_(ids), Message.status == "queued")
        .values(status="suppressed", status_reason=f"suppressed ({s.reason})", updated_at=now)
        .returning(Message.id, Message.contact_id, Message.campaign_id)
    ).all()
    for mid, cid, camp in cancelled:
        record_event(db, "message_suppressed", contact_id=cid, message_id=mid, campaign_id=camp,
                     source="system", actor=actor, data={"reason": s.reason, "suppression_id": s.id})
    # follow-ups stop
    db.execute(
        update(Task)
        .where(Task.contact_id.in_(ids), Task.status == "open", Task.kind == "followup")
        .values(status="cancelled", cancel_reason=f"suppressed ({s.reason})")
    )
    if s.reason in _DNC_STAGE_REASONS:
        for c in contacts:
            if c.pipeline_stage != "do_not_contact":
                record_event(db, "stage_changed", contact_id=c.id, source="system", actor=actor,
                             data={"from": c.pipeline_stage, "to": "do_not_contact", "why": f"suppression:{s.reason}"})
                c.pipeline_stage = "do_not_contact"
    for c in contacts:
        record_event(db, "suppressed", contact_id=c.id, source="system", actor=actor,
                     data={"reason": s.reason, "suppression_id": s.id, "kind": s.kind})


def deactivate(
    db: Session,
    suppression_id: int,
    *,
    actor: str,
    reason: str,
    confirm_value: str,
    force: bool = False,
) -> Suppression:
    row = db.get(Suppression, suppression_id)
    if row is None:
        raise SuppressionError("suppression not found")
    if not reason or len(reason.strip()) < 10:
        raise SuppressionError("a written reason (10+ characters) is required to lift a suppression")
    if confirm_value.strip().lower() != row.value:
        raise SuppressionError("type the exact email/domain to confirm")
    if row.reason in PROTECTED_REASONS and not force:
        raise SuppressionError(
            f"this entry was created because of '{row.reason}'. Lifting it can mean contacting someone who asked "
            "not to be contacted. Resubmit with force=true only if you are certain."
        )
    row.active = False
    row.deactivated_at = datetime.now(timezone.utc)
    row.deactivated_by = actor
    row.deactivate_reason = reason.strip()
    audit(db, "suppression.lift", actor=actor, entity_type="suppression", entity_id=row.id,
          details={"value": row.value, "kind": row.kind, "original_reason": row.reason, "why": reason, "force": force})
    for c in _contacts_for(db, row):
        record_event(db, "suppression_lifted", contact_id=c.id, source="user", actor=actor,
                     data={"suppression_id": row.id, "why": reason})
    db.flush()
    return row


def count_suppressed_contacts(db: Session) -> int:
    """Contacts currently covered by an active email or domain suppression."""
    email_hits = select(Contact.id).join(
        Suppression, and_(Suppression.kind == "email", Suppression.active.is_(True), Suppression.value == Contact.email_normalized)
    )
    domain_hits = select(Contact.id).join(
        Suppression, and_(Suppression.kind == "domain", Suppression.active.is_(True), Suppression.value == Contact.email_domain)
    )
    union = email_hits.union(domain_hits).subquery()
    return db.execute(select(func.count()).select_from(union)).scalar_one()
