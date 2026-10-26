from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Iterable

from sqlalchemy import CheckConstraint, DateTime, MetaData, func, text
from sqlalchemy.orm import DeclarativeBase, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Reusable column types
created_ts = Annotated[datetime, mapped_column(DateTime(timezone=True), server_default=func.now())]
updated_ts = Annotated[
    datetime, mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
]
opt_ts = Annotated[datetime | None, mapped_column(DateTime(timezone=True))]


def check_in(column: str, values: Iterable[str], name: str) -> CheckConstraint:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)


def sql_in(values: Iterable[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


NOW = text("now()")
