from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.db import session_scope

PASSWORD = "correct horse battery staple"


def make_user(email="admin@example.org", role="admin", password=PASSWORD):
    with session_scope() as db:
        security.create_user(db, email, password, role)


class Api:
    """TestClient wrapper that keeps the session cookie and CSRF token like the browser does."""

    def __init__(self, client: TestClient):
        self.c = client
        self.csrf: str | None = None

    def login(self, email="admin@example.org", password=PASSWORD):
        r = self.c.post("/api/auth/login", json={"email": email, "password": password})
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r

    def _h(self, write: bool):
        return {"X-CSRF-Token": self.csrf} if (write and self.csrf) else {}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self._h(True), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h(True), **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self._h(True), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self._h(True), **kw)


@pytest.fixture()
def app_client(database_url):
    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:  # runs lifespan -> seeds defaults
        yield client


@pytest.fixture()
def api(app_client):
    make_user()
    a = Api(app_client)
    assert a.login().status_code == 200
    return a
