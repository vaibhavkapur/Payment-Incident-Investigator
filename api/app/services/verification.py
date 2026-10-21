"""Post-action verification. The incident is resolved only when every check passes; a
missing or contradictory check leaves it unresolved for a human."""
from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from ..db import fetch_all, fetch_one, jsonb
from ..provider.client import ProviderUnavailable, client_for


def verify_recovery(conn: Connection, org_id: str, incident_id: str, job: dict[str, Any], proposal: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool | None, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    order = fetch_one(conn, "select * from orders where org_id = :org and id = :id", org=org_id, id=proposal["order_id"])
    event = fetch_one(conn, "select * from provider_events where org_id = :org and id = :id", org=org_id, id=proposal["provider_event_row_id"])
    snapshot = proposal.get("snapshot") or {}

    check("order_paid", bool(order) and order["status"] == "paid", f"order status is {order['status'] if order else 'missing'}")
    check("order_version_advanced_once", bool(order) and order["version"] == approval["order_version"] + 1, f"version {order['version'] if order else '?'} vs approved {approval['order_version']}")

    applied = fetch_all(conn, "select * from processing_attempts where org_id = :org and provider_event_row_id = :eid and outcome = 'applied'", org=org_id, eid=proposal["provider_event_row_id"])
    check("event_applied_exactly_once", len(applied) == 1, f"{len(applied)} applied processing attempts for the event")
    if applied and applied[0].get("business_op_key"):
        ops = fetch_all(conn, "select * from business_operations where org_id = :org and op_key = :k", org=org_id, k=applied[0]["business_op_key"])
        check("business_operation_recorded_once", len(ops) == 1, f"{len(ops)} business operation rows for {applied[0]['business_op_key']}")

    attempts = fetch_all(conn, "select * from payment_attempts where org_id = :org and order_id = :oid", org=org_id, oid=proposal["order_id"])
    check("no_new_payment_attempts", len(attempts) == snapshot.get("payment_attempt_count"), f"{len(attempts)} attempts now vs {snapshot.get('payment_attempt_count')} at proposal")

    try:
        client, _ = client_for(conn, org_id)
        payment = client.get_payment(conn, org_id, event["provider_payment_ref"])
        check("provider_status_succeeded", payment.status == "succeeded", f"provider ({payment.environment}) status={payment.status}")
        check("provider_amount_matches_order", payment.amount_minor == int(order["amount_minor"]) and payment.currency == order["currency"], f"{payment.amount_minor} {payment.currency} vs order {order['amount_minor']} {order['currency']}")
        count = client.count_payments_for_order(conn, org_id, proposal["order_id"])
        before = snapshot.get("provider_payment_count_for_order")
        if count is None or before is None:
            check("no_new_provider_payment", None, "provider payment count could not be compared")
        else:
            check("no_new_provider_payment", count == before, f"{count} provider payments for order vs {before} at proposal")
    except ProviderUnavailable as exc:
        check("provider_status_succeeded", None, f"provider verification unavailable: {exc}")

    passed = all(c["passed"] is True for c in checks)
    fetch_one(conn, "insert into verification_results (org_id, incident_id, recovery_job_id, passed, checks) values (:org, :inc, :job, :passed, cast(:checks as jsonb)) returning id",
              org=org_id, inc=incident_id, job=job["id"], passed=passed, checks=jsonb(checks))
    return {"passed": passed, "checks": checks}
