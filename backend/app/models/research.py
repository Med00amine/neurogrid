from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import (
    CONFIDENCE_LEVELS,
    FINDING_CATEGORIES,
    FINDING_KINDS,
    FINDING_STATUSES,
    POSITIONINGS,
    RESEARCH_RUN_STATUSES,
)
from app.models.base import Base, check_in, created_ts, opt_ts, updated_ts


class ResearchRun(Base):
    __tablename__ = "research_runs"
    __table_args__ = (
        check_in("status", RESEARCH_RUN_STATUSES, "status"),
        Index("ix_research_runs_company", "company_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    job_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="running")
    mode: Mapped[str] = mapped_column(String(20), default="heuristic")  # heuristic | llm
    model: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[created_ts]
    finished_at: Mapped[opt_ts]
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    search_queries: Mapped[int] = mapped_column(Integer, default=0)
    llm_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd_est: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    company_type: Mapped[str | None] = mapped_column(String(30))
    uncertainties: Mapped[list[str]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    cache_expires_at: Mapped[opt_ts]


class ResearchSource(Base):
    __tablename__ = "research_sources"
    __table_args__ = (Index("ix_research_sources_run", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"))
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(1000))
    final_url: Mapped[str | None] = mapped_column(String(1000))
    title: Mapped[str | None] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(30), default="official_site")  # official_site | search_result
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(100))
    retrieved_at: Mapped[created_ts]
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text)  # untrusted page text, truncated; used to re-verify quotes
    robots_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    injection_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(500))


class ResearchFinding(Base):
    __tablename__ = "research_findings"
    __table_args__ = (
        check_in("category", FINDING_CATEGORIES, "category"),
        check_in("kind", FINDING_KINDS, "kind"),
        check_in("status", FINDING_STATUSES, "status"),
        check_in("confidence", CONFIDENCE_LEVELS, "confidence"),
        Index("ix_research_findings_company", "company_id", "status"),
        Index("ix_research_findings_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"))
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(30))
    statement: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(15), default="fact")
    service_tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    confidence: Mapped[str] = mapped_column(String(10), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    edited_statement: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)  # every cited quote found in its source
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(320))
    reviewed_at: Mapped[opt_ts]
    created_at: Mapped[created_ts]


class FindingEvidence(Base):
    __tablename__ = "finding_evidence"
    __table_args__ = (Index("ix_finding_evidence_finding", "finding_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("research_findings.id", ondelete="CASCADE"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("research_sources.id", ondelete="SET NULL"))
    excerpt: Mapped[str] = mapped_column(Text)
    excerpt_verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (check_in("positioning", POSITIONINGS, "positioning"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    positioning: Mapped[str] = mapped_column(String(30), default="direct_solution")
    # {"company_types": [...], "countries": [...], "keywords": [...], "min_score": int, "stages": [...]}
    filter: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[created_ts]


class LeadScore(Base):
    __tablename__ = "lead_scores"

    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    components: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    research_run_id: Mapped[int | None] = mapped_column(Integer)
    computed_at: Mapped[created_ts]
    updated_at: Mapped[updated_ts]


class UsageRecord(Base):
    """Every paid API call. `cost_basis` says whether the cost is a price-table estimate or provider-reported."""

    __tablename__ = "usage_records"
    __table_args__ = (Index("ix_usage_records_at", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[created_ts]
    provider: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(20))  # llm | search
    purpose: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[float] = mapped_column(Float, default=0)  # e.g. search credits
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    cost_basis: Mapped[str] = mapped_column(String(30), default="estimated_price_table")
    job_id: Mapped[UUID | None] = mapped_column()
    company_id: Mapped[int | None] = mapped_column(Integer)
    contact_id: Mapped[int | None] = mapped_column(Integer)
    campaign_id: Mapped[int | None] = mapped_column(Integer)
