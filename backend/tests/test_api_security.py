from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import reset_settings_cache
from app.db import session_scope
from app.models import Contact, Message, Suppression
from tests.api_helpers import PASSWORD, Api, api, app_client, make_user  # noqa: F401
from tests.factories import make_campaign, make_contact, make_message

PUBLIC = {"/api/auth/login", "/api/auth/logout", "/api/health", "/api/health/ready", "/u/{token}"}  # logout is idempotent and reveals nothing


def _fill(path: str) -> str:
    path = re.sub(r"\{[^}:]*job_id\}", "00000000-0000-0000-0000-000000000000", path)
    return re.sub(r"\{[^}]+\}", "1", path)


def _api_operations(app):
    """(METHOD, path) for every documented API operation, read from the OpenAPI schema."""
    ops = []
    for path, item in app.openapi()["paths"].items():
        if path in PUBLIC or not path.startswith("/api"):
            continue
        for method in item:
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                ops.append((method.upper(), path))
    return ops


def test_every_api_route_requires_a_session(app_client):
    """Sweep the whole route table: no endpoint may answer an anonymous request with anything but 401."""
    ops = _api_operations(app_client.app)
    for method, path in ops:
        r = app_client.request(method, _fill(path), json={} if method != "GET" else None)
        assert r.status_code == 401, f"{method} {path} returned {r.status_code} to an anonymous caller"
    assert len(ops) > 25  # make sure the sweep really covered the API


def test_every_state_changing_route_needs_csrf_and_admin(app_client):
    make_user("viewer@example.org", role="viewer")
    make_user("admin@example.org", role="admin")
    admin = Api(app_client)
    admin.login()
    problems = []
    writes = [(m, p) for m, p in _api_operations(app_client.app) if m != "GET"]
    for method, path in writes:
        r = app_client.request(method, _fill(path), json={})  # session cookie present, NO csrf header
        if r.status_code != 403:
            problems.append((method, path, r.status_code))
    assert writes, "no state-changing routes found"
    assert not problems, f"state-changing routes reachable without a CSRF token: {problems}"


def test_viewer_can_read_but_not_write(app_client):
    make_user("viewer@example.org", role="viewer")
    v = Api(app_client)
    assert v.login("viewer@example.org").status_code == 200
    assert v.get("/api/overview").status_code == 200
    assert v.get("/api/contacts").status_code == 200
    r = v.post("/api/suppressions", json={"value": "x@y.example.com"})
    assert r.status_code == 403
    assert v.get("/api/contacts/export").status_code == 403  # personal-data export is admin only
    assert v.get("/api/audit").status_code == 403


def test_login_flow_cookie_flags_and_logout(app_client):
    make_user()
    a = Api(app_client)
    r = a.login()
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert a.get("/api/auth/me").status_code == 200
    assert a.post("/api/auth/logout").status_code == 200
    assert a.get("/api/auth/me").status_code == 401  # server-side session really ended


def test_wrong_password_and_lockout(app_client):
    make_user()
    a = Api(app_client)
    for _ in range(5):
        assert a.login(password="wrong-password-123").status_code == 401
    r = a.login()  # even the correct password is refused while locked out
    assert r.status_code == 429


def test_unknown_user_gets_same_error_as_wrong_password(app_client):
    make_user()
    a, b = Api(app_client), Api(app_client)
    r1 = a.login("nobody@example.org", "whatever-password-1")
    r2 = b.login("admin@example.org", "whatever-password-1")
    assert r1.status_code == r2.status_code == 401 and r1.json() == r2.json()


def test_security_headers_present(app_client):
    r = app_client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_cors_is_not_open_to_arbitrary_origins(app_client):
    r = app_client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_forged_session_cookie_is_rejected(app_client):
    app_client.cookies.set("ng_session", "A" * 43)
    assert app_client.get("/api/overview").status_code == 401


def test_settings_endpoint_never_exposes_secret_values(database_url, monkeypatch):
    secrets = {
        "SMTP_PASSWORD": "smtp-super-secret-value-123",
        "IMAP_PASSWORD": "imap-super-secret-value-456",
        "ANTHROPIC_API_KEY": "sk-ant-api03-REALLYSECRETKEYVALUE789",
        "TAVILY_API_KEY": "tvly-REALLYSECRETKEYVALUE012",
        "SMTP_HOST": "smtp.example.org", "IMAP_HOST": "imap.example.org",
    }
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()
    from app.main import create_app

    with TestClient(create_app()) as client:
        make_user()
        a = Api(client)
        a.login()
        body = a.get("/api/settings").text
        for k, v in secrets.items():
            if "PASSWORD" in k or "KEY" in k:
                assert v not in body, f"{k} value leaked into /api/settings"
        data = a.get("/api/settings").json()
        assert data["env"]["secrets"]["SMTP_PASSWORD"] is True
        assert data["env"]["secrets"]["ANTHROPIC_API_KEY"] is True
        assert data["env"]["smtp"]["configured"] is True
        # no other endpoint echoes them either
        for path in ("/api/overview", "/api/jobs", "/api/health/ready", "/api/jurisdictions"):
            assert not any(v in a.get(path).text for k, v in secrets.items() if "PASSWORD" in k or "KEY" in k)


def test_log_redaction():
    from app.core.logging_setup import redact

    s = "calling with key sk-ant-api03-ABCDEFGHIJKLMNOP and password=hunter2hunter2 and tvly-ABCDEFGHIJ12345"
    out = redact(s, ["hunter2hunter2"])
    assert "sk-ant-api03" not in out and "hunter2" not in out and "tvly-ABCDEFGHIJ" not in out


def test_unsubscribe_link_requires_valid_signature_and_suppresses(app_client):
    with session_scope() as db:
        c = make_contact(db, email="leaver@acme.example.com")
        camp = make_campaign(db, mode="live", provider="smtp")
        m = make_message(db, c, camp)
        cid, mid = c.id, m.id
    from app.core.security import make_unsubscribe_token

    token = make_unsubscribe_token(mid, cid)
    # a GET must not change anything (mail scanners prefetch links)
    assert app_client.get(f"/u/{token}").status_code == 200
    with session_scope() as db:
        assert db.execute(select(Suppression)).first() is None
    # tampered token
    assert app_client.post(f"/u/{token[:-3]}abc").status_code == 404
    assert app_client.post(f"/u/{make_unsubscribe_token(mid, cid + 1)}").status_code == 404
    # RFC 8058 one-click POST
    r = app_client.post(f"/u/{token}", content="List-Unsubscribe=One-Click", headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    with session_scope() as db:
        s = db.execute(select(Suppression)).scalar_one()
        assert s.value == "leaver@acme.example.com" and s.reason == "unsubscribe" and s.active
        assert db.get(Message, mid).status == "suppressed"  # the queued message is cancelled immediately
        assert db.get(Contact, cid).pipeline_stage == "do_not_contact"


def test_upload_rejects_bad_files_and_path_traversal(api):
    r = api.post("/api/imports/upload", files={"file": ("evil.exe", b"MZ", "application/octet-stream")}, data={"kind": "contacts"})
    assert r.status_code == 400
    r = api.post("/api/imports/start", json={"file_ref": "../../etc/passwd", "kind": "contacts", "mapping": {"email": "email"}})
    assert r.status_code == 404
