"""Recovery proposals. `propose_reprocessing` is the only path that can create one, and it
runs deterministic guard checks that the model cannot bypass. A proposal is bound to the
organization, incident, the original stored event, the order's state version and an expiry."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Connection

from ..config import settings
from ..db import execute, fetch_all, fetch_one, jsonb
from ..provider.client import ProviderUnavailable, client_for
from . import audit

SUPPORTED_EVENT_TYPES = {"payment_intent.succeeded"}


def guard_reprocess(conn: Connection, org_id: str, incident_id: str, provider_event_row_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    snapshot: dict[str, Any] = {}

    def check(name: str, passed: bool, detail: str) -> bool:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
        return bool(passed)

    incident = fetch_one(conn, "select * from incidents where org_id = :org and id = :id", org=org_id, id=incident_id)
    event = fetch_one(conn, "select * from provider_events where org_id = :org and id = :id", org=org_id, id=provider_event_row_id)
    if not check("event_in_organization", bool(event), "stored event exists in this organization" if event else "event not found in organization"):
        return checks, snapshot
    order = fetch_one(conn, "select * from orders where org_id = :org and id = :id", org=org_id, id=incident["order_id"]) if incident and incident.get("order_id") else None
    if not check("incident_has_order", bool(order), "incident is linked to an order" if order else "incident is not linked to an order"):
        return checks, snapshot
    attempts = fetch_all(conn, "select * from payment_attempts where org_id = :org and order_id = :oid order by created_at", org=org_id, oid=order["id"])
    refs = {a["provider_payment_ref"] for a in attempts}
    check("event_belongs_to_order", event["provider_payment_ref"] in refs, f"event payment {event['provider_payment_ref']} is one of the order's attempts" if event["provider_payment_ref"] in refs else "event payment reference does not belong to the incident's order")
    check("event_signature_verified", event["signature_verified"], "event was signature-verified on receipt")
    check("event_type_supported", event["event_type"] in SUPPORTED_EVENT_TYPES, f"event type {event['event_type']}")
    history = fetch_all(conn, "select * from processing_attempts where org_id = :org and provider_event_row_id = :eid order by started_at", org=org_id, eid=event["id"])
    applied = [h for h in history if h["outcome"] == "applied"]
    last = history[-1] if history else None
    check("event_not_already_applied", not applied, "no processing attempt has applied this event" if not applied else "this event was already applied")
    check("last_processing_failed", bool(last) and last["outcome"] == "failed", f"latest processing outcome: {last['outcome'] if last else 'none'}")
    check("order_not_paid", order["status"] in ("pending", "requires_action", "failed"), f"order status is {order['status']}")
    succeeded_refs = set()
    for a in attempts:
        if a["status"] == "succeeded":
            succeeded_refs.add(a["provider_payment_ref"])
    event_obj = (event["payload"] or {}).get("data", {}).get("object", {})
    succeeded_refs.add(event["provider_payment_ref"])
    check("single_successful_payment", len(succeeded_refs) == 1, f"successful payment references for this order: {sorted(succeeded_refs)}")
    try:
        client, _ = client_for(conn, org_id)
        payment = client.get_payment(conn, org_id, event["provider_payment_ref"])
        snapshot["provider_status"] = payment.status
        snapshot["provider_environment"] = payment.environment
        check("provider_confirms_success", payment.status == "succeeded", f"provider ({payment.environment}) status={payment.status}")
        check("amount_matches", payment.amount_minor == int(order["amount_minor"]) and payment.currency == order["currency"] and event_obj.get("amount") == int(order["amount_minor"]),
              f"provider {payment.amount_minor} {payment.currency}; order {order['amount_minor']} {order['currency']}; event {event_obj.get('amount')}")
        snapshot["provider_payment_count_for_order"] = client.count_payments_for_order(conn, org_id, order["id"])
    except ProviderUnavailable as exc:
        check("provider_confirms_success", False, f"provider confirmation unavailable: {exc}")
    snapshot.update({"order_version": order["version"], "order_status": order["status"], "payment_attempt_count": len(attempts), "processing_attempt_count": len(history), "event_delivery_count": event["delivery_count"]})
    return checks, snapshot


def propose_reprocessing(conn: Connection, org_id: str, incident_id: str, provider_event_row_id: str, rationale: str, actor: str = "investigator") -> dict[str, Any]:
    checks, snapshot = guard_reprocess(conn, org_id, incident_id, provider_event_row_id)
    failed = [c for c in checks if not c["passed"]]
    if failed:
        audit.record(conn, org_id, incident_id, "agent", actor, "proposal_rejected_by_guard", {"provider_event_row_id": provider_event_row_id, "failed_checks": failed})
        return {"proposed": False, "reason": "guard checks failed", "failed_checks": failed, "checks": checks}
    incident = fetch_one(conn, "select order_id from incidents where org_id = :org and id = :id", org=org_id, id=incident_id)
    existing = fetch_one(conn, "select * from action_proposals where org_id = :org and incident_id = :inc and status in ('proposed','approved') and provider_event_row_id = :eid", org=org_id, inc=incident_id, eid=provider_event_row_id)
    if existing:
        return {"proposed": True, "proposal_id": existing["id"], "status": existing["status"], "checks": checks, "note": "an active proposal for this event already exists"}
    execute(conn, "update action_proposals set status = 'superseded', updated_at = now() where org_id = :org and incident_id = :inc and status = 'proposed'", org=org_id, inc=incident_id)
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings().APPROVAL_TTL_MINUTES)
    row = fetch_one(
        conn,
        """insert into action_proposals (org_id, incident_id, action_type, provider_event_row_id, order_id, order_version, rationale, guard_checks, snapshot, status, expires_at)
           values (:org, :inc, 'reprocess_stored_event', :eid, :oid, :ver, :rationale, cast(:checks as jsonb), cast(:snapshot as jsonb), 'proposed', :exp) returning id""",
        org=org_id, inc=incident_id, eid=provider_event_row_id, oid=incident["order_id"], ver=snapshot["order_version"], rationale=rationale[:2000], checks=jsonb(checks), snapshot=jsonb(snapshot), exp=expires,
    )
    execute(conn, "update incidents set status = 'awaiting_approval', updated_at = now() where org_id = :org and id = :id", org=org_id, id=incident_id)
    audit.record(conn, org_id, incident_id, "agent", actor, "proposal_created", {"proposal_id": row["id"], "provider_event_row_id": provider_event_row_id, "order_version": snapshot["order_version"]})
    return {"proposed": True, "proposal_id": row["id"], "status": "proposed", "order_version": snapshot["order_version"], "expires_at": expires.isoformat(), "checks": checks}
