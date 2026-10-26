"""Test infrastructure.

* A migrated template database is built once per session by running the real Alembic migrations.
* Every test gets its own database cloned from the template (fast, fully isolated, and the
  append-only triggers stay active, so no test can cheat by truncating history).
* Tests NEVER send real email: ENVIRONMENT=test makes the SMTP provider refuse non-local hosts.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL",
    "postgresql+psycopg://neurogrid:neurogrid_dev_password@127.0.0.1:55432/postgres",
)
TEMPLATE_DB = "neurogrid_test_template"

# Force a safe, deterministic environment before the app modules read settings.
os.environ["ENVIRONMENT"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-test-secret-key-test-secret-key"
os.environ["LIVE_SENDING_ENABLED"] = "false"
for _k in ("SMTP_HOST", "IMAP_HOST", "ANTHROPIC_API_KEY", "TAVILY_API_KEY", "ADMIN_EMAIL", "ADMIN_PASSWORD"):
    os.environ.pop(_k, None)


def _url_for(dbname: str) -> str:
    return make_url(ADMIN_URL).set(database=dbname).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def template_db() -> str:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {TEMPLATE_DB} WITH (FORCE)"))
        c.execute(text(f"CREATE DATABASE {TEMPLATE_DB}"))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    cfg.attributes["sqlalchemy.url"] = _url_for(TEMPLATE_DB)
    command.upgrade(cfg, "head")
    admin.dispose()
    return TEMPLATE_DB


@pytest.fixture()
def database_url(template_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"nt_{uuid.uuid4().hex[:12]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE {name} TEMPLATE {template_db}'))
    url = _url_for(name)
    monkeypatch.setenv("DATABASE_URL", url)

    from app.config import reset_settings_cache
    from app.db import reset_db_cache

    reset_settings_cache()
    reset_db_cache()
    try:
        yield url
    finally:
        reset_db_cache()
        reset_settings_cache()
        with admin.connect() as c:
            c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        admin.dispose()


@pytest.fixture()
def db(database_url: str):
    from app.db import get_sessionmaker

    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
