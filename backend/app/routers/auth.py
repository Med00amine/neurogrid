from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import security
from app.core.deps import Auth, client_ip, read_access
from app.core.events import audit
from app.db import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=500)


def _user_payload(auth_user, csrf: str) -> dict:
    return {"user": {"id": auth_user.id, "email": auth_user.email, "role": auth_user.role}, "csrf_token": csrf}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)):
    try:
        user, token, sess = security.login(db, body.email, body.password, client_ip(request), request.headers.get("user-agent"))
    except security.AuthError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    s = get_settings()
    response.set_cookie(
        security.SESSION_COOKIE, token, max_age=s.session_hours * 3600, httponly=True, secure=s.secure_cookies,
        samesite="lax", path="/",
    )
    audit(db, "auth.login", actor=user.email, actor_user_id=user.id, details={"ip": client_ip(request)})
    return _user_payload(user, sess.csrf_token)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(security.SESSION_COOKIE)
    found = security.session_from_token(db, token)
    # CSRF-protect logout too, but never trap a user in a stale session
    if found and not security.csrf_ok(found[1], request.headers.get("x-csrf-token")):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token")
    security.logout(db, token)
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(auth: Auth = Depends(read_access)):
    return _user_payload(auth.user, auth.session.csrf_token)
