"""Evidence items: every fact the investigator may cite. Each item records its source,
source identifier, retrieval time, event time, ingestion time and environment."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Connection

from ..db import fetch_all, fetch_one, jsonb


def add(
    conn: Connection,
    org_id: str,
    incident_id: str,
    *,
    source: str,
    source_id: str,
    environment: str,
    label: str,
    fields: dict[str, Any],
    event_time: datetime | None = None,
    ingested_at: datetime | None = None,
) -> str:
    row = fetch_one(
        conn,
        """insert into evidence_items (org_id, incident_id, source, source_id, environment, label, fields, event_time, ingested_at)
           values (:org, :inc, :source, :sid, :env, :label, cast(:fields as jsonb), :et, :ia)
           on conflict (incident_id, source, source_id) do update
             set fields = excluded.fields, label = excluded.label, retrieved_at = now(), event_time = excluded.event_time, ingested_at = excluded.ingested_at
           returning id""",
        org=org_id, inc=incident_id, source=source, sid=source_id, env=environment, label=label, fields=jsonb(fields), et=event_time, ia=ingested_at,
    )
    return row["id"]


def for_incident(conn: Connection, org_id: str, incident_id: str) -> list[dict[str, Any]]:
    return fetch_all(conn, "select * from evidence_items where org_id = :org and incident_id = :inc order by retrieved_at, id", org=org_id, inc=incident_id)


def ids_for_incident(conn: Connection, org_id: str, incident_id: str) -> set[str]:
    return {r["id"] for r in fetch_all(conn, "select id from evidence_items where org_id = :org and incident_id = :inc", org=org_id, inc=incident_id)}
