"""Read-only IMAP access (replies, bounces, Sent-folder lookups).

The mailbox is opened READ-ONLY and messages are fetched with BODY.PEEK, so this app never marks, moves or
deletes anything in your mailbox.
"""

from __future__ import annotations

import imaplib
import re
import ssl
from dataclasses import dataclass, field
from typing import Protocol

from app.config import Settings, get_settings

MAX_FETCH_BYTES = 3_000_000


@dataclass
class FetchBatch:
    uidvalidity: int
    items: list[tuple[int, bytes]] = field(default_factory=list)  # (uid, raw message)
    has_more: bool = False


class MailboxClient(Protocol):
    def fetch_new(self, mailbox: str, last_uid: int, known_uidvalidity: int | None, limit: int = 100) -> FetchBatch: ...
    def find_by_message_id(self, message_id: str, mailboxes: list[str]) -> list[dict]: ...
    def check(self) -> dict: ...


class ImapClient:
    def __init__(self, env: Settings | None = None):
        self.env = env or get_settings()

    def _connect(self) -> imaplib.IMAP4:
        e = self.env
        if not e.imap_host:
            raise RuntimeError("IMAP_HOST is not configured")
        if e.environment == "test" and e.imap_host not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError("Refusing to contact a non-local IMAP server while ENVIRONMENT=test")
        if e.imap_ssl:
            m: imaplib.IMAP4 = imaplib.IMAP4_SSL(e.imap_host, e.imap_port, ssl_context=ssl.create_default_context(), timeout=30)
        else:
            m = imaplib.IMAP4(e.imap_host, e.imap_port, timeout=30)
        user = e.imap_username or e.smtp_username or ""
        pw = e.imap_password.get_secret_value() if e.imap_password else ""
        m.login(user, pw)
        return m

    def check(self) -> dict:
        try:
            m = self._connect()
            typ, data = m.select(self.env.imap_mailbox, readonly=True)
            n = int(data[0]) if typ == "OK" and data and data[0] else 0
            m.logout()
            return {"ok": typ == "OK", "messages_in_mailbox": n}
        except (imaplib.IMAP4.error, OSError, ssl.SSLError, RuntimeError) as ex:
            return {"ok": False, "error": f"{type(ex).__name__}: {str(ex)[:200]}"}

    def fetch_new(self, mailbox: str, last_uid: int, known_uidvalidity: int | None, limit: int = 100) -> FetchBatch:
        m = self._connect()
        try:
            typ, _ = m.select(mailbox, readonly=True)
            if typ != "OK":
                raise RuntimeError(f"Cannot open mailbox {mailbox!r}")
            uidvalidity = 0
            typ, data = m.response("UIDVALIDITY")
            if data and data[0]:
                uidvalidity = int(data[0])
            start = last_uid + 1
            if known_uidvalidity is not None and uidvalidity and uidvalidity != known_uidvalidity:
                start = 1  # the server renumbered the mailbox: re-read; duplicates are discarded by Message-ID
            typ, data = m.uid("SEARCH", None, f"UID {start}:*")
            uids = sorted(int(x) for x in (data[0] or b"").split() if int(x) >= start)
            batch = FetchBatch(uidvalidity=uidvalidity, has_more=len(uids) > limit)
            for uid in uids[:limit]:
                typ, msgdata = m.uid("FETCH", str(uid), f"(BODY.PEEK[]<0.{MAX_FETCH_BYTES}>)")
                if typ != "OK":
                    continue
                for part in msgdata:
                    if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                        batch.items.append((uid, bytes(part[1])))
                        break
            return batch
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001
                pass

    def find_by_message_id(self, message_id: str, mailboxes: list[str]) -> list[dict]:
        found = []
        m = self._connect()
        try:
            for mb in mailboxes:
                typ, _ = m.select(f'"{mb}"' if " " in mb else mb, readonly=True)
                if typ != "OK":
                    continue
                safe = re.sub(r'[\\"\r\n]', "", message_id)
                typ, data = m.uid("SEARCH", None, "HEADER", "Message-ID", safe)
                for uid in (data[0] or b"").split():
                    found.append({"mailbox": mb, "uid": int(uid)})
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001
                pass
        return found


@dataclass
class FakeMailbox:
    """In-memory mailbox for tests."""

    messages: dict[int, bytes] = field(default_factory=dict)
    uidvalidity: int = 1
    next_uid: int = 1

    def add(self, raw: bytes) -> int:
        uid = self.next_uid
        self.next_uid += 1
        self.messages[uid] = raw
        return uid

    def fetch_new(self, mailbox: str, last_uid: int, known_uidvalidity: int | None, limit: int = 100) -> FetchBatch:
        start = 1 if (known_uidvalidity is not None and known_uidvalidity != self.uidvalidity) else last_uid + 1
        uids = sorted(u for u in self.messages if u >= start)
        return FetchBatch(self.uidvalidity, [(u, self.messages[u]) for u in uids[:limit]], len(uids) > limit)

    def find_by_message_id(self, message_id: str, mailboxes: list[str]) -> list[dict]:
        return [{"mailbox": "INBOX", "uid": u} for u, raw in self.messages.items() if message_id.encode() in raw]

    def check(self) -> dict:
        return {"ok": True, "messages_in_mailbox": len(self.messages)}


_override: MailboxClient | None = None


def set_mailbox_override(c: MailboxClient | None) -> None:
    global _override
    _override = c


def get_mailbox() -> MailboxClient | None:
    if _override is not None:
        return _override
    e = get_settings()
    if e.imap_host and e.imap_password:
        return ImapClient(e)
    return None
