"""Thin database layer. The API and worker use a privileged connection, so every query
in this codebase filters by org_id explicitly; RLS protects only the frontend path."""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, create_engine, text

from .config import settings

_engine = None


def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings().sqlalchemy_url, pool_pre_ping=True, pool_size=10, max_overflow=10)
    return _engine


@contextmanager
def tx() -> Iterator[Connection]:
    with engine().begin() as conn:
        yield conn


def fetch_one(conn: Connection, sql: str, **params: Any) -> dict[str, Any] | None:
    row = conn.execute(text(sql), params).mappings().first()
    return _clean(dict(row)) if row else None


def fetch_all(conn: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [_clean(dict(r)) for r in conn.execute(text(sql), params).mappings().all()]


def execute(conn: Connection, sql: str, **params: Any) -> int:
    return conn.execute(text(sql), params).rowcount


def jsonb(value: Any) -> str:
    """Serialize for a `cast(:p as jsonb)` parameter."""
    return json.dumps(value, default=_default)


def _default(o: Any) -> Any:
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, (UUID, Decimal)):
        return str(o)
    return str(o)


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, Decimal):
            out[k] = int(v) if v == v.to_integral_value() else float(v)
        elif isinstance(v, list):
            out[k] = [str(x) if isinstance(x, UUID) else x for x in v]
        else:
            out[k] = v
    return out
