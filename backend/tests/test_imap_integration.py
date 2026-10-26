"""Real protocol tests against GreenMail (a real IMAP+SMTP test server on 127.0.0.1).

Start it with:   docker compose --profile test up -d greenmail
They are skipped automatically if the server is not running. Nothing here leaves the machine.
"""

from __future__ import annotations

import imaplib
import os
import smtplib
import socket
import uuid

import pytest
from sqlalchemy import select

from app.config import Settings, reset_settings_cache
from app.core.settings_store import set_group
from app.db import session_scope
from app.models import InboundMessage, Message, Suppression
from app.services.replies import processor
from app.services.replies.imap import ImapClient
from app.services.sending.providers import OutgoingMessage, SMTPProvider
from app.services.seed import seed_defaults
from tests.factories import make_campaign, make_contact, make_message

SMTP_PORT = int(os.environ.get("GREENMAIL_SMTP_PORT", "53025"))
IMAP_PORT = int(os.environ.get("GREENMAIL_IMAP_PORT", "53143"))
ME = "sender@sender.example.org"


def _up() -> bool:
    for port in (SMTP_PORT, IMAP_PORT):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
        except OSError:
            return False
    return True


pytestmark = pytest.mark.skipif(not _up(), reason="GreenMail is not running (docker compose --profile test up -d greenmail)")


@pytest.fixture()
def mailbox_env(database_url, monkeypatch):
    user = f"{uuid.uuid4().hex[:8]}-{ME}"  # a fresh mailbox per test
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(SMTP_PORT))
    monkeypatch.setenv("SMTP_SECURITY", "none")
    monkeypatch.setenv("IMAP_HOST", "127.0.0.1")
    monkeypatch.setenv("IMAP_PORT", str(IMAP_PORT))
    monkeypatch.setenv("IMAP_SSL", "false")
    monkeypatch.setenv("IMAP_USERNAME", user)
    monkeypatch.setenv("IMAP_PASSWORD", "test-password-for-greenmail")
    monkeypatch.setenv("SMTP_FROM_EMAIL", user)
    reset_settings_cache()
    with session_scope() as db:
        seed_defaults(db)
        set_group(db, "sender", {"from_email": user, "display_name": "Test Sender"})
    yield user
    reset_settings_cache()


def deliver(to: str, frm: str, subject: str, body: str, msgid: str, in_reply_to: str | None = None, raw: bytes | None = None) -> None:
    if raw is None:
        h = f"From: {frm}\r\nTo: {to}\r\nSubject: {subject}\r\nMessage-ID: {msgid}\r\nDate: Tue, 03 Nov 2026 14:00:00 +0000\r\n"
        if in_reply_to:
            h += f"In-Reply-To: {in_reply_to}\r\nReferences: {in_reply_to}\r\n"
        raw = (h + "Content-Type: text/plain; charset=utf-8\r\n\r\n" + body).encode()
    with smtplib.SMTP("127.0.0.1", SMTP_PORT, timeout=10) as s:
        s.sendmail(frm, [to], raw)


def unseen_count(user: str) -> int:
    m = imaplib.IMAP4("127.0.0.1", IMAP_PORT)
    m.login(user, "x")
    m.select("INBOX")
    typ, data = m.search(None, "UNSEEN")
    m.logout()
    return len((data[0] or b"").split())


def test_check_connection(mailbox_env):
    assert ImapClient().check()["ok"] is True


def test_reply_round_trip_over_real_smtp_and_imap(mailbox_env):
    me = mailbox_env
    our_id = f"<{uuid.uuid4().hex}@sender.example.org>"
    with session_scope() as db:
        c = make_contact(db, email="pat@acme.example.fr", name="Pat Smith", pipeline_stage="contacted", history_status="previously_contacted")
        camp = make_campaign(db, mode="live", provider="smtp", status="running")
        m = make_message(db, c, camp, rfc_message_id=our_id, subject="Backend capacity", from_email=me)
        m.status = "submitting"; db.flush(); m.status = "submitted"
        mid = m.id
    # 1) our outbound mail really goes through the SMTP provider to the recipient's mailbox
    out = SMTPProvider().send(OutgoingMessage(me, me, "pat@acme.example.fr", "Backend capacity", "Hello Pat", our_id))
    assert out.kind == "accepted"
    # 2) the recipient replies (arrives in OUR mailbox)
    deliver(me, "Pat Smith <pat@acme.example.fr>", "Re: Backend capacity", "Sounds good, can we talk Thursday?", "<reply-1@acme.example.fr>", in_reply_to=our_id)
    # 3) the app polls IMAP read-only
    before = unseen_count(me)
    stats = processor.poll(ImapClient(), "INBOX", session_scope)
    assert stats["processed"] == 1 and stats["replies"] == 1
    assert unseen_count(me) == before  # the mailbox was not modified: message still unread
    with session_scope() as db:
        r = db.execute(select(InboundMessage)).scalar_one()
        assert r.matched_message_id == mid and r.match_method == "in_reply_to" and r.classification == "interested"
        assert db.get(Message, mid).status == "replied"
    # 4) polling again fetches nothing new
    assert processor.poll(ImapClient(), "INBOX", session_scope)["fetched"] == 0


def test_bounce_notification_over_real_imap(mailbox_env):
    me = mailbox_env
    our_id = f"<{uuid.uuid4().hex}@sender.example.org>"
    with session_scope() as db:
        c = make_contact(db, email="gone@acme.example.fr", name="Gone Person", pipeline_stage="contacted", history_status="previously_contacted")
        camp = make_campaign(db, mode="live", provider="smtp", status="running")
        m = make_message(db, c, camp, rfc_message_id=our_id, from_email=me)
        m.status = "submitting"; db.flush(); m.status = "submitted"
        mid = m.id
    dsn = (f"From: MAILER-DAEMON@mx.example.net\r\nTo: {me}\r\nSubject: Undelivered Mail Returned to Sender\r\nMessage-ID: <dsn-{uuid.uuid4().hex}@mx.example.net>\r\n"
           "MIME-Version: 1.0\r\nContent-Type: multipart/report; report-type=delivery-status; boundary=\"B\"\r\n\r\n--B\r\nContent-Type: text/plain\r\n\r\nFailed\r\n\r\n"
           "--B\r\nContent-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; mx.example.net\r\n\r\nFinal-Recipient: rfc822; gone@acme.example.fr\r\nAction: failed\r\nStatus: 5.1.1\r\n"
           f"Diagnostic-Code: smtp; 550 5.1.1 User unknown\r\n\r\n--B\r\nContent-Type: message/rfc822\r\n\r\nMessage-ID: {our_id}\r\nSubject: x\r\n\r\nbody\r\n--B--\r\n").encode()
    deliver(me, "MAILER-DAEMON@mx.example.net", "", "", "", raw=dsn)
    stats = processor.poll(ImapClient(), "INBOX", session_scope)
    assert stats["bounces"] == 1
    with session_scope() as db:
        assert db.get(Message, mid).status == "bounced"
        assert db.execute(select(Suppression).where(Suppression.value == "gone@acme.example.fr", Suppression.reason == "bounce_hard")).scalar_one().active


def test_search_for_a_message_id_in_the_mailbox(mailbox_env):
    me = mailbox_env
    mid = f"<find-{uuid.uuid4().hex}@sender.example.org>"
    deliver(me, me, "Copy of a sent mail", "body", mid)
    found = ImapClient().find_by_message_id(mid, ["INBOX"])
    assert len(found) == 1 and found[0]["mailbox"] == "INBOX"
    assert ImapClient().find_by_message_id("<does-not-exist@x.example>", ["INBOX"]) == []


def test_imap_client_refuses_non_local_hosts_in_tests(database_url, monkeypatch):
    monkeypatch.setenv("IMAP_HOST", "imap.gmail.com")
    reset_settings_cache()
    with pytest.raises(RuntimeError):
        ImapClient()._connect()
