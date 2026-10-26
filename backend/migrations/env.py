from __future__ import annotations

import logging
import os

from alembic import context
from sqlalchemy import create_engine, pool

import app.models  # noqa: F401  (registers tables)
from app.config import get_settings
from app.models.base import Base

config = context.config
if config.config_file_name is not None:
    logging.getLogger("alembic")

target_metadata = Base.metadata


def _url() -> str:
    # tests pass the URL via the Alembic config attribute; everything else uses the app settings
    return config.attributes.get("sqlalchemy.url") or os.environ.get("ALEMBIC_DATABASE_URL") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
