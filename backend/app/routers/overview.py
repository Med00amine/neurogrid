from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import cast, func, select, Date
from sqlalchemy.orm import Session

from app.core.deps import Auth, read_access
from app.db import get_db
from app.models import OutreachEvent
from app.services import metrics

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    return metrics.overview(db)


@router.get("/overview/timeseries")
def timeseries(days: int = Query(14, ge=3, le=90), db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    """Real daily counts (UTC days): live emails accepted by the mail server, and replies received."""
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    since = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    day = cast(OutreachEvent.occurred_at, Date)
    rows = db.execute(
        select(day, OutreachEvent.event_type, func.count()).where(
            OutreachEvent.occurred_at >= since,
            OutreachEvent.event_type.in_(("message_submitted", "reply_received")),
            (OutreachEvent.event_type != "message_submitted") | (OutreachEvent.data["dry_run"].astext == "false"),
        ).group_by(day, OutreachEvent.event_type)
    ).all()
    sub = {d: n for d, t, n in rows if t == "message_submitted"}
    rep = {d: n for d, t, n in rows if t == "reply_received"}
    dates = [start + timedelta(days=i) for i in range(days)]
    return {
        "labels": [d.strftime("%b %d") for d in dates],
        "dates": [d.isoformat() for d in dates],
        "series": [
            {"name": "Emails accepted by mail server", "values": [sub.get(d, 0) for d in dates]},
            {"name": "Replies received", "values": [rep.get(d, 0) for d in dates]},
        ],
    }
