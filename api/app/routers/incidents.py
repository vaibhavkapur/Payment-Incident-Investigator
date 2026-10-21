from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from ..agent import runner
from ..auth import Principal, current_principal
from ..db import execute, fetch_all, fetch_one, tx
from ..services import audit, resolver

router = APIRouter(prefix="/orgs/{org_id}/incidents", tags=["incidents"])


class CreateIncident(BaseModel):
    source_type: Literal["order_id", "payment_ref", "complaint"]
    source_value: str = Field(min_length=1, max_length=200)
    complaint_text: str | None = Field(default=None, max_length=5000)
    investigate: bool = True


@router.post("", status_code=201)
def create_incident(body: CreateIncident, background: BackgroundTasks, p: Principal = Depends(current_principal)) -> dict[str, Any]:
    with tx() as conn:
        order, tried = resolver.resolve(conn, p.org_id, body.source_type, body.source_value, body.complaint_text)
        row = fetch_one(
            conn,
            """insert into incidents (org_id, created_by, assigned_to, status, source_type, source_value, complaint_text, order_id)
               values (:org, :by, :by, 'open', :st, :sv, :ct, :oid) returning *""",
            org=p.org_id, by=p.user_id, st=body.source_type, sv=body.source_value, ct=body.complaint_text, oid=order["id"] if order else None,
        )
        audit.record(conn, p.org_id, row["id"], "operator", p.user_id, "incident_created", {"source_type": body.source_type, "resolved_order_id": order["id"] if order else None, "candidates_tried": tried})
        if body.complaint_text:
            from ..services import evidence

            evidence.add(conn, p.org_id, row["id"], source="operator", source_id="complaint", environment="operator", label="Customer report (untrusted)", fields={"text": body.complaint_text[:5000], "trust": "untrusted"})
    if body.investigate:
        background.add_task(runner.start_investigation, p.org_id, row["id"], p.user_id)
    return row


@router.get("")
def list_incidents(p: Principal = Depends(current_principal)) -> list[dict[str, Any]]:
    with tx() as conn:
        return fetch_all(
            conn,
            """select i.*, o.external_ref, o.amount_minor, o.currency, o.status as order_status, m.display_name as assignee_name
               from incidents i left join orders o on o.id = i.order_id left join memberships m on m.org_id = i.org_id and m.user_id = i.assigned_to
               where i.org_id = :org order by i.created_at desc limit 200""",
            org=p.org_id,
        )


@router.get("/{incident_id}")
def get_incident(incident_id: str, p: Principal = Depends(current_principal)) -> dict[str, Any]:
    with tx() as conn:
        inc = fetch_one(conn, "select * from incidents where org_id = :org and id = :id", org=p.org_id, id=incident_id)
        if not inc:
            raise HTTPException(404, "incident not found")
        order = fetch_one(conn, "select * from orders where org_id = :org and id = :id", org=p.org_id, id=inc["order_id"]) if inc.get("order_id") else None
        attempts = fetch_all(conn, "select * from payment_attempts where org_id = :org and order_id = :oid order by created_at", org=p.org_id, oid=inc["order_id"]) if order else []
        refs = [a["provider_payment_ref"] for a in attempts]
        events = fetch_all(conn, "select id, provider_event_id, event_type, provider_payment_ref, environment, signature_verified, event_time, received_at, last_received_at, delivery_count from provider_events where org_id = :org and provider_payment_ref = any(cast(:refs as text[])) order by event_time", org=p.org_id, refs=refs)
        processing = fetch_all(conn, "select pa.*, pe.provider_event_id from processing_attempts pa join provider_events pe on pe.id = pa.provider_event_row_id where pa.org_id = :org and pe.provider_payment_ref = any(cast(:refs as text[])) order by pa.started_at", org=p.org_id, refs=refs)
        logs = fetch_all(conn, "select * from application_logs where org_id = :org and (order_id = :oid or provider_payment_ref = any(cast(:refs as text[]))) order by ts, id limit 200", org=p.org_id, oid=inc["order_id"], refs=refs) if order else []
        data = {
            "incident": inc,
            "order": order,
            "payment_attempts": attempts,
            "provider_events": events,
            "processing_attempts": processing,
            "evidence": fetch_all(conn, "select * from evidence_items where org_id = :org and incident_id = :id order by retrieved_at, id", org=p.org_id, id=incident_id),
            "diagnoses": fetch_all(conn, "select * from diagnoses where org_id = :org and incident_id = :id order by created_at desc", org=p.org_id, id=incident_id),
            "proposals": fetch_all(conn, "select ap.*, pe.provider_event_id, pe.event_type from action_proposals ap join provider_events pe on pe.id = ap.provider_event_row_id where ap.org_id = :org and ap.incident_id = :id order by ap.created_at desc", org=p.org_id, id=incident_id),
            "approvals": fetch_all(conn, "select a.*, m.display_name as approver_name from approvals a left join memberships m on m.org_id = a.org_id and m.user_id = a.approved_by where a.org_id = :org and a.incident_id = :id order by a.created_at desc", org=p.org_id, id=incident_id),
            "recovery_jobs": fetch_all(conn, "select * from recovery_jobs where org_id = :org and incident_id = :id order by created_at desc", org=p.org_id, id=incident_id),
            "verification_results": fetch_all(conn, "select * from verification_results where org_id = :org and incident_id = :id order by created_at desc", org=p.org_id, id=incident_id),
            "audit": fetch_all(conn, "select * from audit_events where org_id = :org and incident_id = :id order by id", org=p.org_id, id=incident_id),
            "agent_runs": fetch_all(conn, "select id, session_id, mode, model, prompt_version, status, tool_calls, input_tokens, output_tokens, latency_ms, error, created_at from agent_runs where org_id = :org and incident_id = :id order by created_at desc", org=p.org_id, id=incident_id),
            "members": fetch_all(conn, "select user_id, role, display_name from memberships where org_id = :org order by display_name", org=p.org_id),
        }
        data["timeline"] = _timeline(data, logs)
        return data


def _timeline(d: dict[str, Any], logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if d["order"]:
        items.append({"t": d["order"]["created_at"], "kind": "order", "title": f"Order {d['order']['external_ref']} created", "detail": f"{d['order']['amount_minor']} {d['order']['currency']}", "source": "application"})
    for a in d["payment_attempts"]:
        items.append({"t": a["created_at"], "kind": "attempt", "title": f"Payment attempt {a['provider_payment_ref']}", "detail": f"merchant-observed status {a['status']}", "source": "application"})
    for e in d["provider_events"]:
        items.append({"t": e["event_time"], "ingested": e["received_at"], "kind": "event", "title": f"Provider event {e['event_type']}", "detail": f"{e['provider_event_id']} · verified={e['signature_verified']} · deliveries={e['delivery_count']}", "source": e["environment"]})
    for pr in d["processing_attempts"]:
        items.append({"t": pr["started_at"], "kind": "processing", "title": f"Processing {pr['provider_event_id']} via {pr['triggered_by']}: {pr['outcome']}", "detail": pr.get("error") or pr.get("business_op_key") or "", "source": "application"})
    for l in logs:
        if l["event"] in ("processing.started", "processing.applied", "webhook.received"):
            continue
        items.append({"t": l["ts"], "kind": "log", "title": f"[{l['level']}] {l['component']} {l['event']}", "detail": l["message"], "source": "application", "untrusted": True})
    for a in d["audit"]:
        items.append({"t": a["created_at"], "kind": "audit", "title": a["action"].replace("_", " "), "detail": f"{a['actor_type']}:{a['actor_id']}", "source": "workspace"})
    items.sort(key=lambda x: (x["t"] or 0).isoformat() if hasattr(x["t"], "isoformat") else str(x["t"]))
    return items


@router.post("/{incident_id}/investigate")
def investigate(incident_id: str, background: BackgroundTasks, p: Principal = Depends(current_principal)) -> dict[str, Any]:
    with tx() as conn:
        inc = fetch_one(conn, "select status from incidents where org_id = :org and id = :id", org=p.org_id, id=incident_id)
        if not inc:
            raise HTTPException(404, "incident not found")
        if inc["status"] in ("investigating", "recovering", "verifying"):
            raise HTTPException(409, f"incident is {inc['status']}")
    background.add_task(runner.start_investigation, p.org_id, incident_id, p.user_id)
    return {"queued": True}


class Assign(BaseModel):
    user_id: str


@router.post("/{incident_id}/assign")
def assign(incident_id: str, body: Assign, p: Principal = Depends(current_principal)) -> dict[str, Any]:
    with tx() as conn:
        member = fetch_one(conn, "select 1 from memberships where org_id = :org and user_id = :u", org=p.org_id, u=body.user_id)
        if not member:
            raise HTTPException(400, "assignee is not a member of this organization")
        n = execute(conn, "update incidents set assigned_to = :u, updated_at = now() where org_id = :org and id = :id", u=body.user_id, org=p.org_id, id=incident_id)
        if not n:
            raise HTTPException(404, "incident not found")
        audit.record(conn, p.org_id, incident_id, "operator", p.user_id, "incident_assigned", {"assigned_to": body.user_id})
    return {"assigned": True}


class Transition(BaseModel):
    reason: str = Field(default="", max_length=1000)


@router.post("/{incident_id}/escalate")
def escalate(incident_id: str, body: Transition, p: Principal = Depends(current_principal)) -> dict[str, Any]:
    with tx() as conn:
        n = execute(conn, "update incidents set status = 'escalated', updated_at = now() where org_id = :org and id = :id", org=p.org_id, id=incident_id)
        if not n:
            raise HTTPException(404, "incident not found")
        audit.record(conn, p.org_id, incident_id, "operator", p.user_id, "incident_escalated", {"reason": body.reason})
    return {"escalated": True}


@router.post("/{incident_id}/close")
def close_incident(incident_id: str, body: Transition, p: Principal = Depends(current_principal)) -> dict[str, Any]:
    """Operator closes an explained incident that needed no recovery. Incidents repaired by
    recovery are resolved only by passing verification, never by this endpoint."""
    with tx() as conn:
        inc = fetch_one(conn, "select status from incidents where org_id = :org and id = :id", org=p.org_id, id=incident_id)
        if not inc:
            raise HTTPException(404, "incident not found")
        if inc["status"] not in ("needs_review", "escalated", "awaiting_evidence"):
            raise HTTPException(409, f"cannot close an incident in status {inc['status']}")
        pending = fetch_one(conn, "select 1 from recovery_jobs where incident_id = :id and status in ('queued','running')", id=incident_id)
        if pending:
            raise HTTPException(409, "a recovery job is pending")
        execute(conn, "update incidents set status = 'resolved', updated_at = now() where id = :id", id=incident_id)
        audit.record(conn, p.org_id, incident_id, "operator", p.user_id, "incident_closed_by_operator", {"reason": body.reason})
    return {"closed": True}


@router.get("/{incident_id}/report")
def report(incident_id: str, p: Principal = Depends(current_principal)) -> dict[str, Any]:
    d = get_incident(incident_id, p)
    dx = d["diagnoses"][0] if d["diagnoses"] else None
    ev_by_id = {e["id"]: e for e in d["evidence"]}
    lines = [f"# Incident report {incident_id}", "", f"Organization: {p.org_id}", f"Status: {d['incident']['status']}", f"Reported via: {d['incident']['source_type']} = {d['incident']['source_value']}", ""]
    if d["order"]:
        lines += [f"Order: {d['order']['external_ref']} ({d['order']['id']}) status={d['order']['status']} v{d['order']['version']} amount={d['order']['amount_minor']} {d['order']['currency']}", ""]
    if dx:
        lines += ["## Diagnosis", f"Category: {dx['category']}", f"Observed inconsistency: {dx['observed_inconsistency']}", f"Explanation: {dx['explanation']}", f"Limits: {dx['limits']}", f"Next step: {dx['next_step']}", f"Recommended action: {dx['recommended_action']}", f"Produced by: {dx['produced_by']} ({dx['model_version']}, prompt {dx['prompt_version']})", "", "### Supporting evidence"]
        for eid in dx["supporting_evidence_ids"]:
            e = ev_by_id.get(eid)
            lines.append(f"- {eid}: {e['label']} [{e['source']} · {e['environment']} · event_time={e['event_time']} · ingested={e['ingested_at']}]" if e else f"- {eid}: (missing)")
        if dx["conflicting_evidence"]:
            lines += ["", "### Conflicting evidence"] + [f"- {c}" for c in dx["conflicting_evidence"]]
        if dx["missing_evidence"]:
            lines += ["", "### Missing evidence"] + [f"- {m}" for m in dx["missing_evidence"]]
    for pr in d["proposals"]:
        lines += ["", "## Recovery proposal", f"{pr['action_type']} of {pr['provider_event_id']} at order v{pr['order_version']} — status {pr['status']}", f"Rationale: {pr['rationale']}"]
    for v in d["verification_results"]:
        lines += ["", f"## Verification ({'passed' if v['passed'] else 'NOT passed'})"] + [f"- {c['name']}: {c['passed']} — {c['detail']}" for c in v["checks"]]
    lines += ["", "## Audit trail"] + [f"- {a['created_at']} {a['actor_type']}:{a['actor_id']} {a['action']}" for a in d["audit"]]
    lines += ["", "_Sandbox/simulated provider evidence is not real settlement._"]
    return {"markdown": "\n".join(lines), "diagnosis": dx, "verification": d["verification_results"], "evidence": d["evidence"]}


@router.get("/{incident_id}/runs/{run_id}")
def run_trace(incident_id: str, run_id: str, p: Principal = Depends(current_principal)) -> dict[str, Any]:
    with tx() as conn:
        run = fetch_one(conn, "select * from agent_runs where org_id = :org and incident_id = :inc and id = :id", org=p.org_id, inc=incident_id, id=run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run
