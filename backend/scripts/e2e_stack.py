"""Disposable stack for browser tests.

Creates a fresh database, applies the migrations, creates an admin user, serves a local fixture 'company website',
and runs the API (which also serves the built UI from frontend/dist) and the worker. Runs until it is stopped.
Nothing here can send email: no SMTP is configured and LIVE_SENDING_ENABLED is false.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL", "postgresql+psycopg://neurogrid:neurogrid_dev_password@127.0.0.1:55432/postgres")
DB = "neurogrid_e2e"
API_PORT = int(os.environ.get("E2E_API_PORT", "8765"))
SITE_PORT = int(os.environ.get("E2E_SITE_PORT", "8781"))


def main() -> None:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)"))
        c.execute(text(f"CREATE DATABASE {DB}"))
    db_url = ADMIN_URL.rsplit("/", 1)[0] + "/" + DB
    data_dir = tempfile.mkdtemp(prefix="ng-e2e-")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SMTP_", "IMAP_", "ANTHROPIC_", "TAVILY_"))}
    env.update(DATABASE_URL=db_url, ENVIRONMENT="development", ALLOW_PRIVATE_FETCH="true", DATA_DIR=data_dir, LIVE_SENDING_ENABLED="false",
               LOG_JSON="false", LOG_LEVEL="WARNING", PYTHONPATH=str(BACKEND))
    run = lambda *a: subprocess.run([sys.executable, *a], cwd=BACKEND, env=env, check=True)  # noqa: E731
    run("-m", "alembic", "upgrade", "head")
    run("-m", "app.cli", "create-admin", "--email", "e2e@example.org", "--password", "e2e-password-12345")

    from tests.site_fixture import Site

    site = Site(SITE_PORT)
    site.__enter__()
    procs = [
        subprocess.Popen([sys.executable, "-m", "app.jobs.worker"], cwd=BACKEND, env=env),
        subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(API_PORT), "--log-level", "warning"], cwd=BACKEND, env=env),
    ]

    def stop(*_):
        for p in procs:
            p.terminate()
        site.__exit__(None, None, None)
        with admin.connect() as c:
            for p in procs:
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
            c.execute(text(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)"))
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"e2e stack ready: UI/API on http://127.0.0.1:{API_PORT}, fixture company site on http://127.0.0.1:{SITE_PORT}", flush=True)
    while True:
        time.sleep(1)
        if any(p.poll() is not None for p in procs):
            print("a stack process exited", flush=True)
            stop()


if __name__ == "__main__":
    main()
