from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import (
    ELIGIBILITY_STATUSES,
    HISTORY_STATUSES,
    IMPORT_KINDS,
    IMPORT_STATUSES,
    JURISDICTION_RULES,
    PIPELINE_STAGES,
    SUPPRESSION_KINDS,
    SUPPRESSION_REASONS,
)
from app.models.base import Base, check_in, created_ts, opt_ts, updated_ts


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        check_in("kind", IMPORT_KINDS, "kind"),
        check_in("status", IMPORT_STATUSES, "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="contacts")
    filename: Mapped[str] = mapped_column(String(300))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_path: Mapped[str] = mapped_column(String(600))
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    sheet_name: Mapped[str | None] = mapped_column(String(200))
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    provenance_note: Mapped[str | None] = mapped_column(Text)
    history_attestation: Mapped[str] = mapped_column(String(30), default="unknown")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    rows_processed: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_in_file: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_existing: Mapped[int] = mapped_column(Integer, default=0)
    invalid: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    job_id: Mapped[UUID | None] = mapped_column()
    created_by: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[created_ts]
    finished_at: Mapped[opt_ts]


class ImportRowIssue(Base):
    __tablename__ = "import_row_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), index=True)
    source_row: Mapped[int | None] = mapped_column(Integer)
    issue: Mapped[str] = mapped_column(String(100))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str | None] = mapped_column(String(300))
    website_url: Mapped[str | None] = mapped_column(String(600))
    country_code: Mapped[str | None] = mapped_column(String(2))
    company_type: Mapped[str | None] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[created_ts]
    updated_at: Mapped[updated_ts]


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        check_in("eligibility_status", ELIGIBILITY_STATUSES, "eligibility_status"),
        check_in("history_status", HISTORY_STATUSES, "history_status"),
        check_in("pipeline_stage", PIPELINE_STAGES, "pipeline_stage"),
        Index("ix_contacts_company", "company_id"),
        Index("ix_contacts_eligibility", "eligibility_status"),
        Index("ix_contacts_stage", "pipeline_stage"),
        Index("ix_contacts_country", "country_code"),
        Index("ix_contacts_domain", "email_domain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Identity. Unique, always lowercase+trimmed: the database refuses a second row for the same person.
    email_normalized: Mapped[str] = mapped_column(String(320), unique=True)
    email_original: Mapped[str] = mapped_column(String(320))
    email_domain: Mapped[str] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(300))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    company_name_raw: Mapped[str | None] = mapped_column(String(300))
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id", ondelete="SET NULL"))
    source_row: Mapped[int | None] = mapped_column(Integer)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    country_code: Mapped[str | None] = mapped_column(String(2))
    country_source: Mapped[str | None] = mapped_column(String(20))  # tld | research | manual
    role_title: Mapped[str | None] = mapped_column(String(200))
    role_title_source: Mapped[str | None] = mapped_column(String(300))

    eligibility_status: Mapped[str] = mapped_column(String(20), default="unreviewed")
    eligibility_reason: Mapped[str | None] = mapped_column(Text)
    eligibility_updated_at: Mapped[opt_ts]
    history_status: Mapped[str] = mapped_column(String(30), default="unknown")
    consent_basis: Mapped[str | None] = mapped_column(Text)
    consent_recorded_at: Mapped[opt_ts]
    is_freemail: Mapped[bool] = mapped_column(Boolean, default=False)
    is_role_account: Mapped[bool] = mapped_column(Boolean, default=False)

    pipeline_stage: Mapped[str] = mapped_column(String(30), default="new")
    next_action: Mapped[str | None] = mapped_column(String(300))
    next_action_at: Mapped[opt_ts]

    created_at: Mapped[created_ts]
    updated_at: Mapped[updated_ts]


class EligibilityDecision(Base):
    """Auditable record of every eligibility / history / consent change."""

    __tablename__ = "eligibility_decisions"
    __table_args__ = (Index("ix_eligibility_decisions_contact", "contact_id", "decided_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    decided_at: Mapped[created_ts]
    decided_by: Mapped[str] = mapped_column(String(320), default="system")
    field: Mapped[str] = mapped_column(String(40), default="eligibility_status")
    from_value: Mapped[str | None] = mapped_column(String(100))
    to_value: Mapped[str | None] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(Text)


class Suppression(Base):
    """Global do-not-contact list. Imports can only ADD rows; nothing here is ever re-enabled by an import."""

    __tablename__ = "suppressions"
    __table_args__ = (
        UniqueConstraint("kind", "value", name="uq_suppressions_kind_value"),
        check_in("kind", SUPPRESSION_KINDS, "kind"),
        check_in("reason", SUPPRESSION_REASONS, "reason"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), default="email")
    value: Mapped[str] = mapped_column(String(320))  # lowercase email or domain
    reason: Mapped[str] = mapped_column(String(30))
    source: Mapped[str | None] = mapped_column(String(300))
    contact_id: Mapped[int | None] = mapped_column(Integer)
    message_id: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[created_ts]
    created_by: Mapped[str | None] = mapped_column(String(320))
    deactivated_at: Mapped[opt_ts]
    deactivated_by: Mapped[str | None] = mapped_column(String(320))
    deactivate_reason: Mapped[str | None] = mapped_column(Text)


class JurisdictionPolicy(Base):
    __tablename__ = "jurisdiction_policies"
    __table_args__ = (check_in("rule", JURISDICTION_RULES, "rule"),)

    country_code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    rule: Mapped[str] = mapped_column(String(30), default="unknown")
    notes: Mapped[str | None] = mapped_column(Text)
    source_urls: Mapped[list[str]] = mapped_column(JSONB, default=list)
    sources_checked_on: Mapped[date | None] = mapped_column(Date)
    attested_at: Mapped[opt_ts]
    attested_by: Mapped[str | None] = mapped_column(String(320))
    updated_at: Mapped[updated_ts]
