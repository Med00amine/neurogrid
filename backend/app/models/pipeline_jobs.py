from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import DEAL_STATUSES, JOB_ITEM_STATUSES, JOB_STATUSES, TASK_STATUSES
from app.models.base import Base, check_in, created_ts, opt_ts, updated_ts


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        check_in("status", TASK_STATUSES, "status"),
        Index("ix_tasks_due", "status", "due_at"),
        Index("ix_tasks_contact", "contact_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(20), default="manual")  # manual | followup | review | investigate
    due_at: Mapped[opt_ts]
    status: Mapped[str] = mapped_column(String(15), default="open")
    cancel_reason: Mapped[str | None] = mapped_column(String(200))
    source_message_id: Mapped[int | None] = mapped_column(Integer)
    followup_step: Mapped[int | None] = mapped_column(Integer)
    auto: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[created_ts]
    completed_at: Mapped[opt_ts]


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[created_ts]


class Deal(Base):
    __tablename__ = "deals"
    __table_args__ = (check_in("status", DEAL_STATUSES, "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    value_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    status: Mapped[str] = mapped_column(String(10), default="open")
    source_campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"))
    expected_close: Mapped[opt_ts]
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[created_ts]
    updated_at: Mapped[updated_ts]


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        check_in("status", JOB_STATUSES, "status"),
        Index("ix_jobs_claim", "status", "run_after", "priority"),
        Index("ix_jobs_type_status", "type", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(15), default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # An idempotency key makes "enqueue" safe to repeat: the second insert is a no-op.
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_failed: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[opt_ts]
    heartbeat_at: Mapped[opt_ts]
    started_at: Mapped[opt_ts]
    finished_at: Mapped[opt_ts]
    error_message: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(40))
    suggested_action: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[created_ts]


class JobItem(Base):
    __tablename__ = "job_items"
    __table_args__ = (
        UniqueConstraint("job_id", "item_key", name="uq_job_items_job_item"),
        check_in("status", JOB_ITEM_STATUSES, "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    item_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    finished_at: Mapped[opt_ts]
