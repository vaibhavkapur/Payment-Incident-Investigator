"""Approval binds an operator decision to one proposal: organization, incident, original
event, action type, order state version and expiry. Any material change invalidates it."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import Connection

from ..auth import Principal
from ..db import execute, fetch_one
from . import audit


def approve(conn: Connection, principal: Principal, proposal_id: str) -> dict:
    if not principal.can_approve():
        raise HTTPException(403, "approver or admin role required")
    proposal = fetch_one(conn, "select * from action_proposals where org_id = :org and id = :id for update", org=principal.org_id, id=proposal_id)
    if not proposal:
        raise HTTPException(404, "proposal not found")
    if proposal["status"] != "proposed":
        raise HTTPException(409, f"proposal is {proposal['status']}")
    now = datetime.now(timezone.utc)
    if proposal["expires_at"] <= now:
        execute(conn, "update action_proposals set status = 'superseded', updated_at = now() where id = :id", id=proposal_id)
        audit.record(conn, principal.org_id, proposal["incident_id"], "operator", principal.user_id, "approval_refused", {"proposal_id": proposal_id, "reason": "expired"})
        raise HTTPException(409, "proposal expired; a new investigation run is required")
    order = fetch_one(conn, "select version, status from orders where org_id = :org and id = :id", org=principal.org_id, id=proposal["order_id"])
    if not order or order["version"] != proposal["order_version"]:
        execute(conn, "update action_proposals set status = 'superseded', updated_at = now() where id = :id", id=proposal_id)
        execute(conn, "update incidents set status = 'investigating', updated_at = now() where id = :id", id=proposal["incident_id"])
        audit.record(conn, principal.org_id, proposal["incident_id"], "operator", principal.user_id, "approval_refused", {"proposal_id": proposal_id, "reason": "order state changed", "expected_version": proposal["order_version"], "current_version": order["version"] if order else None})
        raise HTTPException(409, "order state changed since the proposal; it has been superseded and must be re-investigated")
    approval = fetch_one(
        conn,
        """insert into approvals (org_id, incident_id, proposal_id, approved_by, action_type, provider_event_row_id, order_id, order_version, expires_at)
           values (:org, :inc, :pid, :by, :atype, :eid, :oid, :ver, :exp) returning id""",
        org=principal.org_id, inc=proposal["incident_id"], pid=proposal_id, by=principal.user_id, atype=proposal["action_type"], eid=proposal["provider_event_row_id"], oid=proposal["order_id"], ver=proposal["order_version"], exp=proposal["expires_at"],
    )
    job = fetch_one(
        conn,
        "insert into recovery_jobs (org_id, incident_id, proposal_id, approval_id, status) values (:org, :inc, :pid, :aid, 'queued') returning id",
        org=principal.org_id, inc=proposal["incident_id"], pid=proposal_id, aid=approval["id"],
    )
    execute(conn, "update action_proposals set status = 'approved', updated_at = now() where id = :id", id=proposal_id)
    execute(conn, "update incidents set status = 'recovering', updated_at = now() where id = :id", id=proposal["incident_id"])
    audit.record(conn, principal.org_id, proposal["incident_id"], "operator", principal.user_id, "proposal_approved", {"proposal_id": proposal_id, "approval_id": approval["id"], "recovery_job_id": job["id"], "order_version": proposal["order_version"]})
    return {"approval_id": approval["id"], "recovery_job_id": job["id"]}


def reject(conn: Connection, principal: Principal, proposal_id: str, reason: str) -> dict:
    proposal = fetch_one(conn, "select * from action_proposals where org_id = :org and id = :id for update", org=principal.org_id, id=proposal_id)
    if not proposal:
        raise HTTPException(404, "proposal not found")
    if proposal["status"] != "proposed":
        raise HTTPException(409, f"proposal is {proposal['status']}")
    execute(conn, "update action_proposals set status = 'rejected', updated_at = now() where id = :id", id=proposal_id)
    execute(conn, "update incidents set status = 'needs_review', updated_at = now() where id = :id", id=proposal["incident_id"])
    audit.record(conn, principal.org_id, proposal["incident_id"], "operator", principal.user_id, "proposal_rejected", {"proposal_id": proposal_id, "reason": reason})
    return {"rejected": True}
