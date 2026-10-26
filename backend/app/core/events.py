"""Append-only event + audit helpers. These only INSERT; the database forbids anything else."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, OutreachEvent


def record_event(
    db: Session,
    event_type: str,
    *,
    contact_id: int | None = None,
    message_id: int | None = None,
    campaign_id: int | None = None,
    source: str = "system",
    actor: str | None = None,
    data: dict[str, Any] | None = None,
) -> OutreachEvent:
    ev = OutreachEvent(
        event_type=event_type,
        contact_id=contact_id,
        message_id=message_id,
        campaign_id=campaign_id,
        source=source,
        actor=actor,
        data=data or {},
    )
    db.add(ev)
    return ev


def audit(
    db: Session,
    action: str,
    *,
    actor: str | None = None,
    actor_user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        action=action,
        actor_label=actor or "system",
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        details=details or {},
    )
    db.add(row)
    return row
