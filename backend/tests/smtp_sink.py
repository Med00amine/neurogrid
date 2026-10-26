"""A real SMTP server on 127.0.0.1 that records mail and can misbehave on demand.

Tests point SMTP_HOST at this sink. The SMTP provider refuses non-local hosts when ENVIRONMENT=test,
so no test can ever reach a real mail server.
"""

from __future__ import annotations

import email
import socket
import threading
from email import policy

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SinkHandler:
    def __init__(self):
        self.messages: list[dict] = []
        self.mode = "ok"
        self.rcpt_modes: dict[str, str] = {}
        self.lock = threading.Lock()

    def _mode(self, rcpt: str | None) -> str:
        return self.rcpt_modes.get(rcpt or "", self.mode)

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        mode = self._mode(address)
        if mode == "rcpt_550":
            return "550 5.1.1 <%s>: User unknown" % address
        if mode == "rcpt_450":
            return "450 4.2.0 Mailbox busy, try again later"
        if mode == "rate":
            return "450 4.7.1 Too many messages, slow down"
        if mode == "quota":
            return "550 5.4.5 Daily user sending quota exceeded"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        mode = self._mode(envelope.rcpt_tos[0] if envelope.rcpt_tos else None)
        if mode == "data_451":
            return "451 4.3.0 Temporary local problem, try later"
        if mode == "data_554":
            return "554 5.7.1 Message rejected as spam by policy"
        msg = email.message_from_bytes(envelope.content, policy=policy.default)
        with self.lock:
            self.messages.append({"from": envelope.mail_from, "to": list(envelope.rcpt_tos), "msg": msg, "raw": envelope.content})
        if mode == "drop":  # accept the data, then vanish before answering: the client cannot know what happened
            server.transport.close()
            return "250 OK"
        return "250 2.0.0 OK queued as SINK%05d" % len(self.messages)


class Sink:
    def __init__(self, auth: tuple[str, str] | None = None):
        self.handler = SinkHandler()
        self.port = free_port()
        kwargs = {}
        if auth:
            def authenticator(server, session, envelope, mechanism, auth_data):
                ok = isinstance(auth_data, LoginPassword) and (auth_data.login.decode(), auth_data.password.decode()) == auth
                return AuthResult(success=ok, handled=ok)  # handled=False makes aiosmtpd send the 535 itself

            kwargs = dict(authenticator=authenticator, auth_required=True, auth_require_tls=False)
        self.controller = Controller(self.handler, hostname="127.0.0.1", port=self.port, **kwargs)

    @property
    def messages(self):
        return self.handler.messages

    def __enter__(self):
        self.controller.start()
        return self

    def __exit__(self, *a):
        self.controller.stop()
