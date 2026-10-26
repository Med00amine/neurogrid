from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import INBOUND_KINDS, REPLY_CLASSES
from app.models.base import Base, check_in, created_ts, opt_ts


class MailboxCursor(Base):
    __tablename__ = "mailbox_cursors"

    mailbox: Mapped[str] = mapped_column(String(200), primary_key=True)
    uidvalidity: Mapped[int | None] = mapped_column(BigInteger)
    last_uid: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[created_ts]


class InboundMessage(Base):
    __tablename__ = "inbound_messages"
    __table_args__ = (
        UniqueConstraint("mailbox", "uidvalidity", "imap_uid", name="uq_inbound_messages_imap"),
        check_in("kind", INBOUND_KINDS, "kind"),
        Index("ix_inbound_messages_matched", "matched_message_id"),
        Index("ix_inbound_messages_received", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mailbox: Mapped[str] = mapped_column(String(200))
    uidvalidity: Mapped[int] = mapped_column(BigInteger, default=0)
    imap_uid: Mapped[int] = mapped_column(BigInteger)
    rfc_message_id: Mapped[str | None] = mapped_column(String(400), unique=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(400))
    references_header: Mapped[str | None] = mapped_column(Text)
    from_email: Mapped[str | None] = mapped_column(String(320))
    from_name: Mapped[str | None] = mapped_column(String(300))
    subject: Mapped[str | None] = mapped_column(String(500))
    received_at: Mapped[opt_ts]
    body_text: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(15), default="other")
    matched_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    matched_contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    match_method: Mapped[str | None] = mapped_column(String(40))
    classification: Mapped[str | None] = mapped_column(String(20))
    classification_source: Mapped[str | None] = mapped_column(String(10))  # rules | llm | user
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    classification_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    dsn: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # parsed bounce/delivery report fields
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[created_ts]
