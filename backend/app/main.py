from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.core.logging_setup import setup_logging
from app.db import session_scope

log = logging.getLogger("api")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = CSP
        if request.url.path.startswith(("/api", "/u/")):
            response.headers["Cache-Control"] = "no-store"
        if get_settings().secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api") and request.url.path != "/api/health":
            log.info("%s %s %s", request.method, request.url.path, response.status_code,
                     extra={"request_id": rid, "path": request.url.path, "status": response.status_code,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
        return response


def bootstrap() -> None:
    """Idempotent first-run setup: seed policies/segments/templates and the admin user from env."""
    from sqlalchemy import func, select

    from app.core import security
    from app.models import User
    from app.services.seed import seed_defaults

    s = get_settings()
    with session_scope() as db:
        seed_defaults(db)
        has_users = db.execute(select(func.count()).select_from(User)).scalar_one() > 0
        if not has_users and s.admin_email and s.admin_password:
            security.create_user(db, s.admin_email, s.admin_password.get_secret_value(), "admin")
            log.info("created initial admin user %s", s.admin_email)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    try:
        bootstrap()
    except Exception:  # pragma: no cover - e.g. migrations not applied yet; /api/health/ready reports it
        log.exception("bootstrap failed (have the migrations been applied?)")
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Neurogrid Client Acquisition Studio",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs" if s.environment != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if s.environment != "production" else None,
    )
    app.add_middleware(SecurityHeaders)
    origins = list(s.allowed_origins)
    if s.environment == "development" and not origins:
        origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    if origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token"],
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": _clean_errors(exc.errors())})

    @app.exception_handler(ValidationError)
    async def _pydantic(_: Request, exc: ValidationError):
        return JSONResponse(status_code=422, content={"detail": _clean_errors(exc.errors())})

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError):
        log.warning("integrity error: %s", str(exc.orig)[:300])
        return JSONResponse(status_code=409, content={"detail": "The database refused this change because it would violate a safety rule "
                                                                 "(for example a duplicate, a suppressed recipient or an illegal status change)."})

    @app.exception_handler(DBAPIError)
    async def _dbapi(_: Request, exc: DBAPIError):
        msg = str(getattr(exc, "orig", exc))
        log.error("database error: %s", msg[:300])
        if "suppression list" in msg or "illegal message status" in msg or "immutable" in msg or "append-only" in msg:
            return JSONResponse(status_code=409, content={"detail": msg.split("\n")[0][:300]})
        return JSONResponse(status_code=500, content={"detail": "Database error"})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    from app.routers import register_routers

    register_routers(app)
    _mount_frontend(app)
    return app


def _clean_errors(errors):
    out = []
    for e in errors:
        loc = ".".join(str(x) for x in e.get("loc", []) if x != "body")
        out.append({"field": loc, "message": e.get("msg", "invalid")})
    return out


def _mount_frontend(app: FastAPI) -> None:
    candidates = [Path(__file__).resolve().parents[1] / "static", Path(__file__).resolve().parents[2] / "frontend" / "dist"]
    dist = next((p for p in candidates if (p / "index.html").exists()), None)
    if dist is None:
        return
    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        f = (dist / full_path).resolve()
        if full_path and f.is_file() and dist.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(dist / "index.html")


app = create_app()
