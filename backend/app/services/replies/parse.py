"""Parse inbound RFC 822 messages: replies, auto-replies and delivery-status notifications (RFC 3464)."""

from __future__ import annotations

import email
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage, Message
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from bs4 import BeautifulSoup

from app.core.text import clean_text, normalize_email

MSGID_RE = re.compile(r"<[^<>\s]+>")


@dataclass
class DSN:
    action: str | None = None  # failed | delayed | delivered | relayed | expanded
    status: str | None = None  # e.g. 5.1.1
    final_recipient: str | None = None
    diagnostic: str | None = None
    original_message_id: str | None = None


@dataclass
class Parsed:
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    from_email: str | None
    from_name: str | None
    subject: str | None
    received_at: datetime | None
    body: str
    is_auto: bool = False
    dsn: DSN | None = None
    headers: dict[str, str] = field(default_factory=dict)


AUTO_SUBJECT_RE = re.compile(
    r"^\s*(auto(matic)?[- ]?(reply|response)|out of (the )?office|abwesenheit|abwesenheitsnotiz|automatische antwort|réponse automatique|"
    r"absence du bureau|risposta automatica|respuesta automática|vacation|on leave|away from)", re.I)


def _first_id(value: str | None) -> str | None:
    if not value:
        return None
    m = MSGID_RE.search(value)
    return m.group(0) if m else None


def _body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain",))
    text = None
    if part is not None:
        try:
            text = part.get_content()
        except (LookupError, UnicodeDecodeError):
            text = part.get_payload(decode=True).decode("utf-8", "replace") if part.get_payload(decode=True) else ""
    if not text:
        html = msg.get_body(preferencelist=("html",))
        if html is not None:
            try:
                raw = html.get_content()
            except (LookupError, UnicodeDecodeError):
                raw = ""
            text = BeautifulSoup(raw, "lxml").get_text("\n")
    return (text or "").replace("\r\n", "\n")[:60_000]


def _parse_dsn(msg: EmailMessage) -> DSN | None:
    ctype = msg.get_content_type()
    if ctype != "multipart/report" and "delivery-status" not in (msg.get("Content-Type") or "").lower():
        # some servers send DSNs as plain multipart/mixed with a delivery-status part
        if not any(p.get_content_type() == "message/delivery-status" for p in msg.walk()):
            return None
    dsn = DSN()
    for part in msg.walk():
        pt = part.get_content_type()
        if pt == "message/delivery-status":
            # the payload is a list of header blocks: [per-message, per-recipient, ...]
            blocks = part.get_payload()
            for b in blocks if isinstance(blocks, list) else []:
                if isinstance(b, Message):
                    dsn.action = dsn.action or (b.get("Action") or "").strip().lower() or None
                    dsn.status = dsn.status or (b.get("Status") or "").strip() or None
                    fr = b.get("Final-Recipient") or b.get("Original-Recipient")
                    if fr and not dsn.final_recipient:
                        dsn.final_recipient = normalize_email(fr.split(";", 1)[-1].strip())
                    dsn.diagnostic = dsn.diagnostic or clean_text(b.get("Diagnostic-Code"), 400)
        elif pt in ("message/rfc822", "text/rfc822-headers") and not dsn.original_message_id:
            inner = part.get_payload()
            if isinstance(inner, list) and inner and isinstance(inner[0], Message):
                dsn.original_message_id = _first_id(inner[0].get("Message-ID"))
            elif isinstance(inner, str):
                m = re.search(r"^Message-ID:\s*(<[^>\s]+>)", inner, re.I | re.M)
                dsn.original_message_id = m.group(1) if m else None
    if not (dsn.action or dsn.status or dsn.original_message_id):
        return None
    return dsn


def parse_message(raw: bytes) -> Parsed:
    msg: EmailMessage = email.message_from_bytes(raw, policy=policy.default)  # type: ignore[assignment]
    name, addr = parseaddr(str(msg.get("From") or ""))
    from_email = normalize_email(addr)
    refs = MSGID_RE.findall(str(msg.get("References") or ""))
    try:
        received = parsedate_to_datetime(str(msg.get("Date"))) if msg.get("Date") else None
        if received and received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        received = None
    subject = clean_text(str(msg.get("Subject") or ""), 500)
    auto_sub = (str(msg.get("Auto-Submitted") or "no")).strip().lower()
    prec = (str(msg.get("Precedence") or "")).strip().lower()
    is_auto = (auto_sub not in ("no", "")) or prec in ("auto_reply", "bulk", "junk") or bool(msg.get("X-Autoreply") or msg.get("X-Autorespond")) \
        or bool(subject and AUTO_SUBJECT_RE.search(subject))
    dsn = _parse_dsn(msg)
    keep = {k: str(msg.get(k))[:300] for k in ("From", "To", "Subject", "Date", "Message-ID", "In-Reply-To", "References", "Auto-Submitted",
                                                "Precedence", "Return-Path", "List-Unsubscribe") if msg.get(k)}
    return Parsed(
        message_id=_first_id(str(msg.get("Message-ID") or "")), in_reply_to=_first_id(str(msg.get("In-Reply-To") or "")), references=refs,
        from_email=from_email, from_name=clean_text(name, 300), subject=subject, received_at=received, body=_body_text(msg),
        is_auto=is_auto, dsn=dsn, headers=keep,
    )


# --- quoted text removal ------------------------------------------------------------------------------------------

_QUOTE_START = re.compile(
    r"^\s*(on .{5,200}wrote:|le .{5,200}a écrit\s*:|am .{5,200}schrieb.*:|il giorno .{5,200}ha scritto:|el .{5,200}escribió:|"
    r"-{2,}\s*original message\s*-{2,}|-{2,}\s*message d'origine\s*-{2,}|-{2,}\s*ursprüngliche nachricht\s*-{2,}|"
    r"from:\s.+|von:\s.+|de\s*:\s.+@.+|_{5,}|-----\s*forwarded)", re.I)


def new_text_only(body: str) -> str:
    """The part the person actually wrote: strip quoted lines, 'On … wrote:' blocks and the signature delimiter."""
    out: list[str] = []
    for line in body.splitlines():
        if line.strip() == "--" or line.startswith("-- "):
            break
        if _QUOTE_START.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()
