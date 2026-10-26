from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import DRAFT_STATUSES, LENGTHS, POSITIONINGS, TONES
from app.models.base import Base, check_in, created_ts, opt_ts, updated_ts


class EmailTemplate(Base):
    __tablename__ = "email_templates"
    __table_args__ = (
        check_in("positioning", POSITIONINGS, "positioning"),
        check_in("tone", TONES, "tone"),
        check_in("length", LENGTHS, "length"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    positioning: Mapped[str] = mapped_column(String(30), default="direct_solution")
    tone: Mapped[str] = mapped_column(String(20), default="direct")
    length: Mapped[str] = mapped_column(String(20), default="short")
    instructions: Mapped[str | None] = mapped_column(Text)
    subject_hint: Mapped[str | None] = mapped_column(String(200))
    cta_text: Mapped[str | None] = mapped_column(String(400))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[created_ts]
    updated_at: Mapped[updated_ts]


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (
        check_in("status", DRAFT_STATUSES, "status"),
        # one live draft per contact and sequence step
        Index(
            "uq_active_draft_per_contact_step",
            "contact_id",
            "step",
            unique=True,
            postgresql_where=text("status IN ('generating','needs_review','approved')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("email_templates.id", ondelete="SET NULL"))
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("segments.id", ondelete="SET NULL"))
    step: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="needs_review")
    current_version_id: Mapped[int | None] = mapped_column(Integer)
    approved_version_id: Mapped[int | None] = mapped_column(Integer)
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(320))
    approved_at: Mapped[opt_ts]
    created_by: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[created_ts]
    updated_at: Mapped[updated_ts]


class DraftVersion(Base):
    __tablename__ = "draft_versions"
    __table_args__ = (UniqueConstraint("draft_id", "version_no", name="uq_draft_versions_draft_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(300))
    body_text: Mapped[str] = mapped_column(Text)
    # [{"text": str, "type": "company_fact|sender_fact|offer|cta", "finding_ids": [int]}]
    claims: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    blockers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    source: Mapped[str] = mapped_column(String(20), default="template")  # template | llm | user
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd_est: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[created_ts]
