"""Authentication primitives: password hashing, server-side sessions, CSRF tokens, signed links."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuthSession, LoginAttempt, User

_ph = PasswordHasher()  # argon2id with the library's current recommended parameters
# A valid hash of a random value, used to keep login timing similar when the user does not exist.
_DUMMY_HASH = _ph.hash(secrets.token_hex(16))

SESSION_COOKIE = "ng_session"
MAX_FAILED_PER_EMAIL = 5
MAX_FAILED_PER_IP = 25
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LENGTH = 12


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if password.lower() in {"password1234", "123456789012", "administrator"}:
        raise ValueError("password is too common")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _throttled(db: Session, key: str, limit: int) -> bool:
    since = datetime.now(timezone.utc) - timedelta(minutes=LOCKOUT_MINUTES)
    n = db.execute(
        select(func.count()).select_from(LoginAttempt).where(
            LoginAttempt.key == key, LoginAttempt.success.is_(False), LoginAttempt.at >= since
        )
    ).scalar_one()
    return n >= limit


def login(db: Session, email: str, password: str, ip: str | None, user_agent: str | None) -> tuple[User, str, AuthSession]:
    email = email.strip().lower()
    ekey, ikey = f"email:{email}", f"ip:{ip or 'unknown'}"
    if _throttled(db, ekey, MAX_FAILED_PER_EMAIL) or _throttled(db, ikey, MAX_FAILED_PER_IP):
        raise AuthError("Too many failed attempts. Try again in a few minutes.", 429)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    ok = verify_password(user.password_hash if user else _DUMMY_HASH, password)
    if not (ok and user and user.is_active):
        db.add(LoginAttempt(key=ekey, success=False))
        db.add(LoginAttempt(key=ikey, success=False))
        db.commit()  # the failure must persist even though the request errors out
        raise AuthError("Invalid email or password")
    s = get_settings()
    token = secrets.token_urlsafe(32)
    sess = AuthSession(
        user_id=user.id,
        token_hash=_token_hash(token),
        csrf_token=secrets.token_urlsafe(24),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=s.session_hours),
        last_seen_at=datetime.now(timezone.utc),
        ip=ip,
        user_agent=(user_agent or "")[:300],
    )
    db.add(sess)
    user.last_login_at = datetime.now(timezone.utc)
    db.add(LoginAttempt(key=ekey, success=True))
    db.execute(delete(AuthSession).where(AuthSession.expires_at < datetime.now(timezone.utc)))
    db.flush()
    return user, token, sess


def session_from_token(db: Session, token: str | None) -> tuple[User, AuthSession] | None:
    if not token:
        return None
    sess = db.execute(select(AuthSession).where(AuthSession.token_hash == _token_hash(token))).scalar_one_or_none()
    if sess is None or sess.expires_at < datetime.now(timezone.utc):
        return None
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        return None
    sess.last_seen_at = datetime.now(timezone.utc)
    return user, sess


def logout(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))


def create_user(db: Session, email: str, password: str, role: str = "admin") -> User:
    validate_new_password(password)
    email = email.strip().lower()
    if "@" not in email:
        raise ValueError("invalid email")
    user = User(email=email, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def csrf_ok(session: AuthSession, header_value: str | None) -> bool:
    return bool(header_value) and hmac.compare_digest(session.csrf_token, header_value)


# --- signed unsubscribe links ------------------------------------------------------------------------------------
# token = base64url(message_id ":" contact_id) + "." + base64url(HMAC-SHA256(secret, payload))
# The token identifies the recipient but carries no personal data and cannot be forged without the secret.

def _sig(payload: bytes) -> bytes:
    key = get_settings().secret_key.get_secret_value().encode()
    return hmac.new(key, b"unsubscribe:" + payload, hashlib.sha256).digest()[:18]


def make_unsubscribe_token(message_id: int, contact_id: int) -> str:
    payload = f"{message_id}:{contact_id}".encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + base64.urlsafe_b64encode(_sig(payload)).decode().rstrip("=")


def parse_unsubscribe_token(token: str) -> tuple[int, int] | None:
    try:
        p, s = token.split(".", 1)
        payload = base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))
        sig = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        if not hmac.compare_digest(sig, _sig(payload)):
            return None
        mid, cid = payload.decode().split(":")
        return int(mid), int(cid)
    except Exception:
        return None
