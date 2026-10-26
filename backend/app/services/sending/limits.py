"""Pacing rules: sending window, daily and hourly caps, minimum interval, provider backoff.

Counters are computed from the messages table (the source of truth), so they survive restarts and
are shared by every campaign that uses the same provider account.
Dry runs rehearse the whole flow but ignore pacing so a rehearsal finishes in seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.settings_store import get_group
from app.models import Campaign, Message

ATTEMPT_STATUSES = ("submitting", "submitted", "delivered", "bounced", "replied", "unknown", "rejected")


@dataclass
class Gate:
    allowed: bool
    reason: str | None = None
    retry_at: datetime | None = None


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def local_day_start(now: datetime, tz: ZoneInfo) -> datetime:
    local = now.astimezone(tz)
    return datetime.combine(local.date(), time(0, 0), tzinfo=tz).astimezone(timezone.utc)


def next_window_open(now: datetime, window: dict) -> datetime:
    tz = _tz(window.get("tz"))
    days = set(window.get("days", [0, 1, 2, 3, 4]))
    start = _hhmm(window.get("start", "09:00"))
    local = now.astimezone(tz)
    for add in range(0, 8):
        d = local.date() + timedelta(days=add)
        if d.weekday() in days:
            cand = datetime.combine(d, start, tzinfo=tz)
            if cand > local:
                return cand.astimezone(timezone.utc)
    return now + timedelta(days=1)


def in_window(now: datetime, window: dict) -> bool:
    tz = _tz(window.get("tz"))
    local = now.astimezone(tz)
    if local.weekday() not in set(window.get("days", [0, 1, 2, 3, 4])):
        return False
    return _hhmm(window.get("start", "09:00")) <= local.time() < _hhmm(window.get("end", "17:00"))


def attempts_since(db: Session, since: datetime, dry: bool) -> int:
    return db.execute(
        select(func.count()).select_from(Message).where(Message.is_dry_run.is_(dry), Message.submission_started_at >= since,
                                                        Message.status.in_(ATTEMPT_STATUSES))
    ).scalar_one()


def last_attempt_at(db: Session, dry: bool) -> datetime | None:
    return db.execute(select(func.max(Message.submission_started_at)).where(Message.is_dry_run.is_(dry))).scalar_one()


def send_gate(db: Session, campaign: Campaign, now: datetime) -> Gate:
    """May this campaign start one more send right now?"""
    if campaign.mode == "dry_run":
        return Gate(True)
    sending = get_group(db, "sending")
    if campaign.backoff_until and campaign.backoff_until > now:
        return Gate(False, "provider backoff", campaign.backoff_until)
    window = campaign.window or {}
    if not in_window(now, window):
        return Gate(False, "outside the sending window", next_window_open(now, window))
    tz = _tz(window.get("tz"))
    daily_cap = min(campaign.daily_cap, sending.daily_cap)
    day_start = local_day_start(now, tz)
    if attempts_since(db, day_start, False) >= daily_cap:
        return Gate(False, f"daily cap of {daily_cap} reached", next_window_open(now, window))
    hourly_cap = min(campaign.hourly_cap, sending.hourly_cap)
    if attempts_since(db, now - timedelta(hours=1), False) >= hourly_cap:
        return Gate(False, f"hourly cap of {hourly_cap} reached", now + timedelta(minutes=5))
    gap = max(campaign.min_interval_seconds, sending.min_interval_seconds)
    last = last_attempt_at(db, False)
    if last and (now - last).total_seconds() < gap:
        return Gate(False, "minimum interval between sends", last + timedelta(seconds=gap))
    return Gate(True)


def retry_delay(attempt_no: int) -> timedelta:
    """Exponential backoff for failures that provably did not send: 5 min, 15 min, 45 min, ..."""
    return timedelta(minutes=5 * (3 ** max(0, attempt_no - 1)))
