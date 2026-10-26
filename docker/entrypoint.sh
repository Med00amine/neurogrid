#!/bin/sh
# api    : apply database migrations, then serve the API + UI
# worker : wait until the migrations are applied, then run background jobs and the sender
set -e
case "$1" in
  api)
    echo "applying database migrations..."
    python -m alembic upgrade head
    exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}" --log-level warning
    ;;
  worker)
    echo "waiting for the database schema..."
    python - <<'PY'
import time, sys
from sqlalchemy import text
from app.db import get_engine
for i in range(120):
    try:
        with get_engine().connect() as c:
            if c.execute(text("select 1 from alembic_version")).first():
                sys.exit(0)
    except Exception:
        pass
    time.sleep(2)
print("database schema not ready after 4 minutes", file=sys.stderr); sys.exit(1)
PY
    exec python -m app.jobs.worker
    ;;
  *)
    exec "$@"
    ;;
esac
