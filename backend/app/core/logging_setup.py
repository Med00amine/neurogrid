"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

from app.config import get_settings

# generic key shapes that should never reach a log line even if they are not in our own settings
_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(password|passwd|secret|api[_-]?key|authorization)(\"?\s*[:=]\s*\"?)([^\s\",;]{4,})"),
]
REDACTED = "[REDACTED]"


def redact(text: str, secrets: list[str] | None = None) -> str:
    for s in secrets or []:
        if s:
            text = text.replace(s, REDACTED)
    text = _PATTERNS[0].sub(REDACTED, text)
    text = _PATTERNS[1].sub(REDACTED, text)
    text = _PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return text


class RedactingFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._secrets: list[str] | None = None

    def _secret_values(self) -> list[str]:
        if self._secrets is None:
            try:
                self._secrets = get_settings().secret_values()
            except Exception:  # pragma: no cover
                self._secrets = []
        return self._secrets

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        record.msg = redact(msg, self._secret_values())
        record.args = ()
        if record.exc_info:
            # render + redact the traceback now so handlers cannot print the raw one
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info), self._secret_values())
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "job_id", "path", "status", "duration_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    s = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    if s.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(s.log_level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
