"""Approved-recovery worker. Claims one queued job at a time with row locks, revalidates the
approval against current state, executes the single supported action by asking the merchant
to reprocess the stored event, verifies the result, and records everything. A worker restart
is safe: a stale `running` job is reclaimed, and the merchant deduplicates by recovery job id
and by business-operation key."""
from __future__ import annotations

import logging
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import Connection

from ..config import settings
from ..db import execute, fetch_one, jsonb, tx
from . import audit, verification

log = logging.getLogger("worker")


def claim_job(conn: Connection, worker_id: str) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        """update recovery_jobs set status = 'running', locked_at = now(), locked_by = :w, attempt_count = attempt_count + 1, updated_at = now()
           where id = (
             select id from recovery_jobs
             where status = 'queued' or (status = 'running' and locked_at < now() - make_interval(secs => :stale))
             order by created_at for update skip locked limit 1)
           returning *""",
        w=worker_id, stale=settings().WORKER_STALE_LOCK_SECONDS,
    )


def revalidate(conn: Connection, job: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None, dict[str, Any] | None]:
    org = job["org_id"]
    approval = fetch_one(conn, "select * from approvals where org_id = :org and id = :id", org=org, id=job["approval_id"])
    proposal = fetch_one(conn, "select * from action_proposals where org_id = :org and id = :id", org=org, id=job["proposal_id"])
    if not approval or not proposal:
        return False, "approval or proposal missing", None, None
    now = datetime.now(timezone.utc)
    if approval["revoked_at"] is not None:
        return False, f"approval revoked: {approval['revoke_reason']}", proposal, approval
    if approval["expires_at"] <= now:
        return False, "approval expired", proposal, approval
    if approval["proposal_id"] != proposal["id"] or approval["incident_id"] != job["incident_id"] or proposal["incident_id"] != job["incident_id"]:
        return False, "approval does not bind to this job's proposal and incident", proposal, approval
    if proposal["status"] not in ("approved",):
        return False, f"proposal status is {proposal['status']}", proposal, approval
    if approval["action_type"] != proposal["action_type"] or approval["provider_event_row_id"] != proposal["provider_event_row_id"]:
        return False, "approval action does not match proposal", proposal, approval
    event = fetch_one(conn, "select id from provider_events where org_id = :org and id = :id", org=org, id=proposal["provider_event_row_id"])
    if not event:
        return False, "original event no longer exists in organization", proposal, approval
    order = fetch_one(conn, "select version, status from orders where org_id = :org and id = :id", org=org, id=proposal["order_id"])
    if not order:
        return False, "order missing", proposal, approval
    prior = fetch_one(conn, "select id from processing_attempts where org_id = :org and recovery_job_id = :job", org=org, job=job["id"])
    if order["version"] != approval["order_version"] and not prior:
        return False, f"order version changed ({order['version']} != approved {approval['order_version']})", proposal, approval
    return True, "ok", proposal, approval


def execute_job(job: dict[str, Any], worker_id: str) -> dict[str, Any]:
    org, incident_id = job["org_id"], job["incident_id"]
    with tx() as conn:
        ok, reason, proposal, approval = revalidate(conn, job)
        if not ok:
            execute(conn, "update recovery_jobs set status = 'rejected', result = cast(:r as jsonb), updated_at = now() where id = :id", id=job["id"], r=jsonb({"reason": reason}))
            if proposal:
                execute(conn, "update action_proposals set status = 'superseded', updated_at = now() where id = :id and status = 'approved'", id=proposal["id"])
            execute(conn, "update incidents set status = 'needs_review', updated_at = now() where id = :id", id=incident_id)
            audit.record(conn, org, incident_id, "worker", worker_id, "recovery_rejected_on_revalidation", {"recovery_job_id": job["id"], "reason": reason})
            return {"status": "rejected", "reason": reason}
        audit.record(conn, org, incident_id, "worker", worker_id, "recovery_started", {"recovery_job_id": job["id"], "provider_event_row_id": proposal["provider_event_row_id"], "approved_order_version": approval["order_version"]})

    s = settings()
    try:
        resp = httpx.post(
            f"{s.MERCHANT_URL}/internal/reprocess",
            json={"org_id": org, "provider_event_row_id": proposal["provider_event_row_id"], "recovery_job_id": job["id"]},
            headers={"x-worker-token": s.WORKER_TOKEN}, timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as exc:  # noqa: BLE001
        with tx() as conn:
            execute(conn, "update recovery_jobs set status = 'failed', result = cast(:r as jsonb), updated_at = now() where id = :id", id=job["id"], r=jsonb({"error": str(exc)}))
            execute(conn, "update action_proposals set status = 'failed', updated_at = now() where id = :id", id=proposal["id"])
            execute(conn, "update incidents set status = 'failed', updated_at = now() where id = :id", id=incident_id)
            audit.record(conn, org, incident_id, "worker", worker_id, "recovery_execution_failed", {"recovery_job_id": job["id"], "error": str(exc)})
        return {"status": "failed", "error": str(exc)}

    with tx() as conn:
        audit.record(conn, org, incident_id, "worker", worker_id, "recovery_executed", {"recovery_job_id": job["id"], "merchant_result": result})
        execute(conn, "update incidents set status = 'verifying', updated_at = now() where id = :id", id=incident_id)
        outcome = verification.verify_recovery(conn, org, incident_id, job, proposal, approval)
        final = "succeeded" if outcome["passed"] else "failed"
        execute(conn, "update recovery_jobs set status = :st, result = cast(:r as jsonb), updated_at = now() where id = :id", id=job["id"], st=final, r=jsonb({"merchant_result": result, "verification": outcome}))
        execute(conn, "update action_proposals set status = :st, updated_at = now() where id = :id", id=proposal["id"], st="executed" if outcome["passed"] else "failed")
        new_status = "resolved" if outcome["passed"] else "needs_review"
        execute(conn, "update incidents set status = :st, updated_at = now() where id = :id", id=incident_id, st=new_status)
        audit.record(conn, org, incident_id, "worker", worker_id, "verification_recorded", {"recovery_job_id": job["id"], "passed": outcome["passed"], "incident_status": new_status})
    return {"status": final, "verification": outcome, "merchant_result": result}


def run_once(worker_id: str | None = None) -> dict[str, Any] | None:
    worker_id = worker_id or f"{socket.gethostname()}:{threading.get_ident()}"
    with tx() as conn:
        job = claim_job(conn, worker_id)
    if not job:
        return None
    log.info("claimed recovery job %s", job["id"])
    return {"job_id": job["id"], **execute_job(job, worker_id)}


def run_forever(stop: threading.Event | None = None, worker_id: str | None = None) -> None:
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            if run_once(worker_id) is None:
                stop.wait(settings().WORKER_POLL_SECONDS)
        except Exception:  # noqa: BLE001
            log.exception("worker iteration failed")
            stop.wait(settings().WORKER_POLL_SECONDS)
