"""FastAPI dependencies: database session, authentication, authorisation and CSRF."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core import security
from app.db import get_db
from app.models import AuthSession, User


@dataclass
class Auth:
    user: User
    session: AuthSession

    @property
    def label(self) -> str:
        return self.user.email

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"


def read_access(request: Request, db: Session = Depends(get_db)) -> Auth:
    """Any signed-in user may read."""
    found = security.session_from_token(db, request.cookies.get(security.SESSION_COOKIE))
    if found is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    user, sess = found
    return Auth(user=user, session=sess)


def write_access(request: Request, auth: Auth = Depends(read_access)) -> Auth:
    """State-changing request: admin role AND a valid CSRF header."""
    if not auth.is_admin:
        raise HTTPException(status_code=403, detail="This action needs the admin role")
    if not security.csrf_ok(auth.session, request.headers.get("x-csrf-token")):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token")
    return auth


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
