"""Mail providers.

`SendOutcome.retry_safe` is True ONLY when we know the server never accepted the message data
(connection, TLS, login, MAIL FROM, RCPT TO or an explicit refusal of DATA). Anything that happens after
the message body has been transmitted and before the server's final answer is `unknown`.
"""

from __future__ import annotations

import email.policy
import re
import smtplib
import socket
import ssl
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate

from app.config import Settings, get_settings


@dataclass
class OutgoingMessage:
    from_header: str
    from_email: str
    to_email: str
    subject: str
    body: str
    rfc_message_id: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class SendOutcome:
    kind: str  # accepted | rejected | temporary_failure | auth_failure | pre_send_failure | unknown
    stage: str = ""
    smtp_code: int | None = None
    response: str | None = None
    provider_message_id: str | None = None
    error_category: str | None = None  # network | auth | rate_limited | mailbox_unavailable | policy_rejected | content_rejected | ambiguous | config
    retry_safe: bool = False
    dsn_requested: bool = False


class Provider:
    name = "base"
    supports_delivery_events = False

    def send(self, msg: OutgoingMessage) -> SendOutcome:  # pragma: no cover - interface
        raise NotImplementedError

    def capabilities(self) -> dict:  # pragma: no cover
        return {}


class DryRunProvider(Provider):
    """Never touches the network. Used for rehearsals and by default."""

    name = "dry_run"

    def send(self, msg: OutgoingMessage) -> SendOutcome:
        return SendOutcome("accepted", "dry_run", 250, "dry run: nothing was sent", f"dryrun-{uuid.uuid4().hex[:12]}")

    def capabilities(self) -> dict:
        return {"sends_real_email": False, "delivery_confirmation": False, "bounce_detection": False}


# --- SMTP --------------------------------------------------------------------------------------------------------------

_RATE_RE = re.compile(r"\brate[- ]?limit|\brate\b|too many|throttl|quota|limit exceeded|sending limit|\bdaily\b", re.I)
_POLICY_RE = re.compile(r"spam|blocked|blacklist|blocklist|reputation|policy|denied|not allowed|rejected due|dmarc|spf|dkim|unsolicited|abuse", re.I)
_MAILBOX_RE = re.compile(r"user unknown|no such user|does not exist|unknown user|mailbox (not found|unavailable|does not exist)|invalid (recipient|mailbox)|recipient (rejected|address rejected)|no mailbox", re.I)


def classify_reply(code: int, resp: str) -> tuple[str, str]:
    """-> (outcome kind, error category) for a 4xx/5xx SMTP reply received BEFORE the message was accepted."""
    text = resp or ""
    m = re.search(r"\b[245]\.\d\.\d{1,3}\b", text)
    ecode = m.group(0) if m else ""
    if code in (530, 534, 535) or ecode.startswith("5.7.8"):
        return "auth_failure", "auth"
    if 400 <= code < 500:
        if _RATE_RE.search(text) or ecode.startswith("4.7."):
            return "temporary_failure", "rate_limited"
        return "temporary_failure", "network"
    # 5xx: the server refused this message or recipient
    if ecode.startswith("5.4.5") or re.search(r"daily|quota|sending limit|too many", text, re.I):
        return "temporary_failure", "rate_limited"  # provider-side limit: not accepted, and not the recipient's fault
    if ecode.startswith(("5.1.1", "5.1.2", "5.1.3", "5.1.6", "5.2.1")) or _MAILBOX_RE.search(text):
        return "rejected", "mailbox_unavailable"
    if _POLICY_RE.search(text) or ecode.startswith("5.7."):
        return "rejected", "policy_rejected"
    if code == 554:
        return "rejected", "content_rejected"
    if code in (550, 551, 553):
        return "rejected", "mailbox_unavailable"
    return "rejected", "policy_rejected"


def _refusal(stage: str, code: int, resp: str) -> SendOutcome:
    """Outcome for an explicit refusal received before the body was accepted. Nothing was sent."""
    kind, cat = classify_reply(code, resp)
    if stage == "mail_from" and kind == "rejected":
        # the server refuses OUR sender address: a configuration problem, not a verdict on the recipient
        return SendOutcome("pre_send_failure", stage, code, resp, error_category="config", retry_safe=True)
    return SendOutcome(kind, stage, code, resp, error_category=cat, retry_safe=kind in ("temporary_failure", "auth_failure"))


def build_mime(msg: OutgoingMessage) -> EmailMessage:
    m = EmailMessage(policy=email.policy.SMTP)
    m["From"] = msg.from_header
    m["To"] = msg.to_email
    m["Subject"] = msg.subject
    m["Date"] = formatdate(localtime=False)
    m["Message-ID"] = msg.rfc_message_id
    for k, v in msg.headers.items():
        if k.lower() in ("from", "to", "subject", "date", "message-id", "bcc", "cc"):
            continue
        m[k] = v
    m.set_content(msg.body, charset="utf-8", cte="quoted-printable")
    return m


class SMTPProvider(Provider):
    name = "smtp"

    def __init__(self, env: Settings | None = None):
        self.env = env or get_settings()

    def capabilities(self) -> dict:
        return {"sends_real_email": True, "delivery_confirmation": False, "bounce_detection": "via IMAP delivery-status messages",
                "note": "SMTP success only means your mail server accepted the message for relay."}

    def _guard(self) -> None:
        e = self.env
        if not e.smtp_host:
            raise RuntimeError("SMTP_HOST is not configured")
        if e.environment == "test" and e.smtp_host not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError("Refusing to contact a non-local SMTP server while ENVIRONMENT=test")

    def check_connection(self) -> dict:
        """Connect + (STARTTLS) + log in, then quit. Sends nothing."""
        self._guard()
        e = self.env
        try:
            c = self._connect()
            ext = sorted(k for k in getattr(c, "esmtp_features", {}))
            c.quit()
            return {"ok": True, "extensions": ext[:20]}
        except smtplib.SMTPAuthenticationError:
            return {"ok": False, "error": "The server rejected the username/password"}
        except (smtplib.SMTPException, OSError, ssl.SSLError) as ex:
            return {"ok": False, "error": f"{type(ex).__name__}: {str(ex)[:200]}"}

    def _connect(self) -> smtplib.SMTP:
        e = self.env
        ctx = ssl.create_default_context()
        if e.smtp_security == "ssl":
            c: smtplib.SMTP = smtplib.SMTP_SSL(e.smtp_host, e.smtp_port, timeout=e.smtp_timeout_seconds, context=ctx)
            c.ehlo()
        else:
            c = smtplib.SMTP(e.smtp_host, e.smtp_port, timeout=e.smtp_timeout_seconds)
            c.ehlo()
            if e.smtp_security == "starttls":
                c.starttls(context=ctx)
                c.ehlo()
        if e.smtp_username and e.smtp_password:
            c.login(e.smtp_username, e.smtp_password.get_secret_value())
        return c

    def send(self, msg: OutgoingMessage) -> SendOutcome:
        try:
            self._guard()
        except RuntimeError as ex:
            return SendOutcome("pre_send_failure", "config", None, str(ex), error_category="config", retry_safe=True)
        try:
            mime = build_mime(msg)
            raw = mime.as_bytes()
        except (ValueError, UnicodeError) as ex:
            return SendOutcome("rejected", "compose", None, f"could not build the message: {ex}", error_category="content_rejected")

        stage = "connect"
        conn: smtplib.SMTP | None = None
        try:
            try:
                conn = self._connect()
            except smtplib.SMTPAuthenticationError as ex:
                return SendOutcome("auth_failure", "auth", ex.smtp_code, _s(ex.smtp_error), error_category="auth", retry_safe=True)
            except smtplib.SMTPResponseException as ex:
                kind, cat = classify_reply(ex.smtp_code, _s(ex.smtp_error))
                return SendOutcome("auth_failure" if kind == "auth_failure" else "temporary_failure", "connect", ex.smtp_code, _s(ex.smtp_error),
                                   error_category=cat, retry_safe=True)
            except (OSError, ssl.SSLError, smtplib.SMTPException, socket.timeout) as ex:
                return SendOutcome("pre_send_failure", "connect", None, f"{type(ex).__name__}: {str(ex)[:200]}", error_category="network", retry_safe=True)

            dsn = conn.has_extn("dsn")
            stage = "mail_from"
            code, resp = conn.mail(msg.from_email, ["RET=HDRS"] if dsn else [])
            if code != 250:
                return _refusal(stage, code, _s(resp))
            stage = "rcpt_to"
            code, resp = conn.rcpt(msg.to_email, ["NOTIFY=SUCCESS,FAILURE,DELAY"] if dsn else [])
            if code not in (250, 251):
                conn.rset()
                return _refusal(stage, code, _s(resp))
            stage = "data"
            try:
                code, resp = conn.data(raw)
            except smtplib.SMTPDataError as ex:  # server refused DATA before we sent the body
                return _refusal(stage, ex.smtp_code, _s(ex.smtp_error))
            except (smtplib.SMTPServerDisconnected, OSError, ssl.SSLError, socket.timeout) as ex:
                # the body may or may not have been accepted: NEVER retry blindly
                return SendOutcome("unknown", "after_data", None, f"{type(ex).__name__}: {str(ex)[:200]}", error_category="ambiguous", retry_safe=False, dsn_requested=dsn)
            if code == 250:
                return SendOutcome("accepted", stage, code, _s(resp), provider_message_id=_queue_id(_s(resp)), dsn_requested=dsn)
            kind, cat = classify_reply(code, _s(resp))
            # a negative FINAL reply after the body is an explicit "not accepted"
            return SendOutcome(kind, "after_data", code, _s(resp), error_category=cat, retry_safe=kind == "temporary_failure", dsn_requested=dsn)
        finally:
            if conn is not None:
                try:
                    conn.quit()
                except Exception:  # noqa: BLE001
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass


def _s(v) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v or "")


def _queue_id(resp: str) -> str | None:
    """Best-effort: many servers answer '250 2.0.0 Ok: queued as ABC123' / '250 OK ... - gsmtp'."""
    m = re.search(r"queued as ([A-Za-z0-9._-]+)", resp, re.I) or re.search(r"\bid[= ]([A-Za-z0-9._-]{6,})", resp, re.I)
    if m:
        return m.group(1)
    toks = resp.split()
    return toks[-3] if len(toks) >= 4 and re.fullmatch(r"[A-Za-z0-9._-]{8,}", toks[-3]) else None


_override: Provider | None = None


def set_provider_override(p: Provider | None) -> None:
    global _override
    _override = p


def get_provider(name: str) -> Provider:
    if _override is not None and name != "dry_run":
        return _override
    if name == "smtp":
        return SMTPProvider()
    return DryRunProvider()
