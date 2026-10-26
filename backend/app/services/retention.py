"""Data minimisation: remove stored third-party text after its retention period.

* research page text is only needed to re-verify quotes; findings and their short excerpts are kept
* inbound reply bodies contain other people's words and personal data; the metadata is kept
Suppressions, outreach events and the audit trail are never purged (they are what protects people).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.events import audit
from app.core.settings_store import get_group
from app.db import session_scope
from app.jobs.worker import periodic
from app.models import InboundMessage, ResearchSource

log = logging.getLogger("retention")


def purge(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    g = get_group(db, "general")
    src = db.execute(
        update(ResearchSource).where(ResearchSource.text.is_not(None), ResearchSource.retrieved_at < now - timedelta(days=g.source_text_retention_days))
        .values(text=None, note="page text removed after the retention period").returning(ResearchSource.id)
    ).all()
    inb = db.execute(
        update(InboundMessage).where(InboundMessage.body_text.is_not(None), InboundMessage.created_at < now - timedelta(days=g.inbound_retention_days))
        .values(body_text=None).returning(InboundMessage.id)
    ).all()
    if src or inb:
        audit(db, "retention.purge", details={"source_texts": len(src), "reply_bodies": len(inb)})
    return {"source_texts": len(src), "reply_bodies": len(inb)}


@periodic("retention_purge", 6 * 3600)
def _periodic() -> None:
    with session_scope() as db:
        r = purge(db)
        if r["source_texts"] or r["reply_bodies"]:
            log.info("retention purge: %s", r)
