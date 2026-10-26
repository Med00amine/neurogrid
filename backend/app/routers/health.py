from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def liveness():
    return {"status": "ok"}


@router.get("/ready")
def readiness(response: Response, db: Session = Depends(get_db)):
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
        current = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        import os

        cfg = Config(os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "..", "migrations"))
        head = ScriptDirectory.from_config(cfg).get_current_head()
        checks["migrations"] = "ok" if current == head else f"behind (db={current}, code={head})"
    except Exception as e:  # noqa: BLE001
        checks["database"] = f"error: {type(e).__name__}"
    ok = all(v == "ok" for v in checks.values()) and "database" in checks
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "degraded", "checks": checks}
