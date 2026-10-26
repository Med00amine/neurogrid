from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import CAMPAIGN_MODES, CAMPAIGN_STATUSES, MESSAGE_NON_BLOCKING_STATUSES, MESSAGE_STATUSES
from app.models.base import Base, check_in, created_ts, opt_ts, sql_in, updated_ts


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        check_in("mode", CAMPAIGN_MODES, "mode"),
        check_in("status", CAMPAIGN_STATUSES, "status"),
        CheckConstraint("daily_cap >= 1 AND daily_cap <= 500", name="daily_cap_range"),
        CheckConstraint("min_interval_seconds >= 10", name="min_interval"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(10), default="dry_run")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    sequence_step: Mapped[int] = mapped_column(Integer, default=1)
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("segments.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(20), default="dry_run")
    daily_cap: Mapped[int] = mapped_column(Integer, default=20)
    hourly_cap: Mapped[int] = mapped_column(Integer, default=5)
    min_interval_seconds: Mapped[int] = mapped_column(Integer, default=120)
    window: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    estimate: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    sender_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    paused_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[created_ts]
    confirmed_by: Mapped[str | None] = mapped_column(String(320))
    confirmed_at: Mapped[opt_ts]
    started_at: Mapped[opt_ts]
    finished_at: Mapped[opt_ts]
    updated_at: Mapped[updated_ts]


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (
        UniqueConstraint("campaign_id", "contact_id", name="uq_campaign_recipients_campaign_contact"),
        check_in("status", ("included", "excluded"), "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    draft_version_id: Mapped[int | None] = mapped_column(ForeignKey("draft_versions.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10), default="included")
    exclusion_reason: Mapped[str | None] = mapped_column(Text)


class Message(Base):
    """One email to one recipient for one sequence step.

    The partial unique index below is the core duplicate-send guard: at most ONE non-dry-run,
    non-provably-unsent message may exist per (contact, step) across ALL campaigns.
    """

    __tablename__ = "messages"
    __table_args__ = (
        check_in("status", MESSAGE_STATUSES, "status"),
        CheckConstraint(
            "(is_dry_run AND provider = 'dry_run') OR (NOT is_dry_run AND provider <> 'dry_run')",
            name="dry_run_provider",
        ),
        Index(
            "uq_live_message_per_contact_step",
            "contact_id",
            "sequence_step",
            unique=True,
            postgresql_where=text(f"NOT is_dry_run AND status NOT IN ({sql_in(MESSAGE_NON_BLOCKING_STATUSES)})"),
        ),
        Index("ix_messages_campaign_status", "campaign_id", "status"),
        Index("ix_messages_queue", "status", "not_before"),
        Index("ix_messages_provider_msgid", "provider_message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="RESTRICT"))
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="RESTRICT"), index=True)
    draft_version_id: Mapped[int | None] = mapped_column(ForeignKey("draft_versions.id", ondelete="SET NULL"))
    sequence_step: Mapped[int] = mapped_column(Integer, default=1)
    is_dry_run: Mapped[bool] = mapped_column(Boolean, default=True)

    # Immutable snapshot of exactly what is (or would be) sent.
    recipient_email: Mapped[str] = mapped_column(String(320))
    from_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300))
    body_text: Mapped[str] = mapped_column(Text)
    extra_headers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    status: Mapped[str] = mapped_column(String(20), default="queued")
    status_reason: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(20), default="dry_run")
    provider_message_id: Mapped[str | None] = mapped_column(String(300))
    rfc_message_id: Mapped[str] = mapped_column(String(300), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    last_error_category: Mapped[str | None] = mapped_column(String(40))
    last_error: Mapped[str | None] = mapped_column(Text)
    cost_usd_est: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)

    queued_at: Mapped[created_ts]
    submission_started_at: Mapped[opt_ts]
    submitted_at: Mapped[opt_ts]
    delivered_at: Mapped[opt_ts]
    bounced_at: Mapped[opt_ts]
    replied_at: Mapped[opt_ts]
    updated_at: Mapped[updated_ts]


class SendAttempt(Base):
    __tablename__ = "send_attempts"
    __table_args__ = (
        UniqueConstraint("message_id", "attempt_no", name="uq_send_attempts_message_attempt"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[created_ts]
    finished_at: Mapped[opt_ts]
    outcome: Mapped[str | None] = mapped_column(String(30))
    stage: Mapped[str | None] = mapped_column(String(30))
    smtp_code: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(40))


class OutreachEvent(Base):
    """Append-only history (UPDATE/DELETE/TRUNCATE rejected by trigger)."""

    __tablename__ = "outreach_events"
    __table_args__ = (
        Index("ix_outreach_events_contact", "contact_id", "occurred_at"),
        Index("ix_outreach_events_type", "event_type", "occurred_at"),
        Index("ix_outreach_events_message", "message_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    occurred_at: Mapped[created_ts]
    event_type: Mapped[str] = mapped_column(String(60))
    contact_id: Mapped[int | None] = mapped_column(Integer)
    message_id: Mapped[int | None] = mapped_column(Integer)
    campaign_id: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20), default="system")  # system | user | provider | imap
    actor: Mapped[str | None] = mapped_column(String(320))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
