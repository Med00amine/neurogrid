from __future__ import annotations

import importlib

from fastapi import FastAPI

# Routers are added stage by stage; a missing optional module is skipped so the app stays runnable.
ROUTER_MODULES = [
    "auth", "health", "overview", "contacts", "imports", "jobs", "compliance", "settings_api", "public",
    "research", "leads", "studio", "campaigns", "replies", "pipeline",
]


def register_routers(app: FastAPI) -> None:
    for name in ROUTER_MODULES:
        try:
            mod = importlib.import_module(f"app.routers.{name}")
        except ModuleNotFoundError as e:
            if e.name != f"app.routers.{name}":
                raise
            continue
        app.include_router(mod.router)
