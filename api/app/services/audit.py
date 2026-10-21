from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from ..db import execute, jsonb


def record(conn: Connection, org_id: str, incident_id: str | None, actor_type: str, actor_id: str, action: str, details: dict[str, Any] | None = None) -> None:
    execute(
        conn,
        "insert into audit_events (org_id, incident_id, actor_type, actor_id, action, details) values (:org, :inc, :at, :aid, :action, cast(:details as jsonb))",
        org=org_id, inc=incident_id, at=actor_type, aid=actor_id, action=action, details=jsonb(details or {}),
    )
