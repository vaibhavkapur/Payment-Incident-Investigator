"""Rule-based fallback investigator. It uses exactly the same scoped tools and the same
diagnosis/proposal services as the ADK agent, so the safety properties of the system do not
depend on the model. It is used when no Gemini credential is configured and in CI."""
from __future__ import annotations

from typing import Any

from ..db import tx
from ..services import diagnosis as diagnosis_service
from ..services import proposals as proposal_service
from ..services import tools as t


def investigate(org_id: str, incident_id: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
    def call(name: str, fn, *args):
        with tx() as conn:
            out = fn(t.Scope(conn, org_id, incident_id), *args)
        trace.append({"type": "tool", "name": name, "args": list(args), "result_keys": sorted(out.keys()), "evidence_ids": out.get("evidence_ids", [])})
        return out

    order_res = call("get_order", t.get_order)
    if "error" in order_res:
        call("request_more_information", t.request_more_information, "Which order or payment reference does this report concern?", "order link: the report could not be matched to an order in this organization")
        return _record(org_id, incident_id, trace, category="unresolved_identifier", obs="The report could not be linked to an order in this organization.",
                       exp="No order, external reference or provider payment reference in the report matched this organization's records.", limits="Nothing about the payment can be established until the order is identified.",
                       support=[], conflicting=[], missing=["order identifier or provider payment reference"], next_step="Ask the reporter for the order number or the provider payment reference, then re-run the investigation.", action="request_information")
    order = order_res["order"]
    attempts = call("get_payment_attempts", t.get_payment_attempts)["attempts"]
    provider: dict[str, dict[str, Any]] = {}
    gaps: list[str] = []
    events: list[dict[str, Any]] = []
    for a in attempts:
        res = call("get_provider_payment", t.get_provider_payment, a["provider_payment_ref"])
        if "payment" in res:
            provider[a["provider_payment_ref"]] = {**res["payment"], "evidence_id": res["evidence_ids"][0]}
        else:
            gaps.append(res["evidence_ids"][0])
    events = call("list_webhook_events", t.list_webhook_events, None)["events"]
    history = call("get_processing_history", t.get_processing_history, None)["attempts"]
    logs = call("query_application_logs", t.query_application_logs, None, None, None, None, 50)["logs"]

    support = [order_res["evidence_ids"][0]] + [a["evidence_id"] for a in attempts]
    conflicting: list[str] = []
    suspicious = [l for l in logs if any(k in (l.get("message") or "").lower() for k in ("skip approval", "bypass approval", "without approval", "reprocess immediately", "issue a refund", "ignore previous"))]
    if suspicious:
        conflicting.append(f"{len(suspicious)} log/note entries contain embedded instructions; treated as untrusted text and ignored")
        support += [l["evidence_id"] for l in suspicious]
    env = next(iter(provider.values()))["environment"] if provider else "unknown"
    env_note = f" Provider evidence is {env}; it does not establish bank settlement."

    if attempts and not provider:
        call("request_more_information", t.request_more_information, "Provider lookup failed; retry later or check the provider dashboard.", "current provider record for " + ", ".join(a["provider_payment_ref"] for a in attempts))
        return _record(org_id, incident_id, trace, category="provider_evidence_unavailable", obs="The provider record could not be retrieved, so the provider state of the payment is unknown.",
                       exp="The merchant application shows order status '%s' and %d payment attempt(s), but every provider lookup failed." % (order["status"], len(attempts)), limits="Without a provider record no claim about whether the customer was charged can be made.",
                       support=support + gaps, conflicting=conflicting, missing=["provider payment record"], next_step="Retry the provider lookup when the provider API is available; do not retry the charge or reprocess anything until provider state is known.", action="request_information")

    succeeded = {ref: p for ref, p in provider.items() if p["status"] == "succeeded"}
    support += [p["evidence_id"] for p in provider.values()]
    support += [e["evidence_id"] for e in events]
    support += [h["evidence_id"] for h in history]
    error_logs = [l for l in logs if l["level"] == "error"]
    support += [l["evidence_id"] for l in error_logs][:5]

    if len(succeeded) >= 2 or order["status"] == "needs_review":
        return _record(org_id, incident_id, trace, category="multiple_payment_attempts", obs=f"{len(succeeded)} successful provider payments exist for one order ({', '.join(succeeded)}).",
                       exp="The customer retried checkout and more than one payment succeeded at the provider. The application flagged the order for review instead of absorbing the second payment." + env_note,
                       limits="Which payment the customer intended, and whether a refund is due, requires a human decision; this system never issues refunds.", support=support, conflicting=conflicting, missing=[],
                       next_step="Escalate to a payments lead to decide on refunding the duplicate payment; do not reprocess any event.", action="escalate")

    if len(succeeded) == 1:
        ref, p = next(iter(succeeded.items()))
        succ_events = [e for e in events if e["provider_payment_ref"] == ref and e["event_type"] == "payment_intent.succeeded"]
        if order["status"] == "paid":
            dup = any(e["delivery_count"] > 1 for e in succ_events)
            if order.get("checkout_response") == "error_page":
                cat, obs, exp = "lost_checkout_response_recovered", "The customer saw an error page although the provider accepted the payment.", "The provider confirms the payment succeeded; the checkout response was lost (error page), and the verified webhook later marked the order paid. No repair is needed." + env_note
            elif order.get("checkout_response") in ("failure", "requires_action"):
                cat, obs, exp = "late_webhook_applied", "The order page reported failure or pending, but the order is paid.", "The provider's success event arrived after the order page was rendered; the application applied it and the order is now paid." + env_note
            elif dup:
                cat, obs, exp = "duplicate_webhook_deduplicated", "The same provider event was delivered more than once.", "Duplicate deliveries were recorded on the single stored event and deduplicated by business-operation identity; the order was marked paid once." + env_note
            else:
                cat, obs, exp = "no_inconsistency", "The provider and the application agree that the order is paid.", "Provider status succeeded, a verified webhook was stored and applied once, and the order is paid." + env_note
            return _record(org_id, incident_id, trace, category=cat, obs=obs, exp=exp, limits="Provider success does not prove settlement; customer-facing communication may still be needed.", support=support, conflicting=conflicting, missing=[],
                           next_step="Inform the customer that the order is paid; no recovery action is required.", action="none")
        if not succ_events:
            call("request_more_information", t.request_more_information, "Does the provider show a delivery attempt for the success event to the merchant endpoint?", "provider-side webhook delivery log for " + ref)
            return _record(org_id, incident_id, trace, category="webhook_not_received", obs=f"The provider reports {ref} succeeded but the application never stored a verified success event and the order is '{order['status']}'.",
                           exp="No signature-verified payment_intent.succeeded event exists for this payment, so the merchant could not update the order. The failure is in webhook delivery or endpoint availability, not in event processing." + env_note,
                           limits="This system cannot see the provider's delivery attempts and does not fabricate events; it can only reprocess events that were actually stored.", support=support, conflicting=conflicting, missing=["stored provider event", "provider webhook delivery log"],
                           next_step="Check the provider dashboard for the event's delivery attempts and resend it from the provider; never create a new charge.", action="request_information")
        ev = succ_events[-1]
        ev_hist = [h for h in history if h["provider_event_row_id"] == ev["provider_event_row_id"]]
        if ev_hist and ev_hist[-1]["outcome"] == "failed" and not any(h["outcome"] == "applied" for h in ev_hist):
            proposal = _propose(org_id, incident_id, ev["provider_event_row_id"], f"Provider confirms {ref} succeeded; verified event {ev['provider_event_id']} was stored but processing failed ({ev_hist[-1]['error']}); order still {order['status']}.", trace)
            if proposal.get("proposed"):
                return _record(org_id, incident_id, trace, category="processing_failure_after_receipt", obs=f"The provider shows {ref} succeeded and a verified success event was stored, but the order is still '{order['status']}'.",
                               exp=f"Processing of stored event {ev['provider_event_id']} failed inside the application ({ev_hist[-1]['error']}), so the order was never marked paid. Reprocessing that existing event through the normal handler repairs the order without touching the provider." + env_note,
                               limits="Settlement is not established; the root cause of the handler failure is inferred from the application error only.", support=support, conflicting=conflicting, missing=[],
                               next_step="Review and approve the reprocessing proposal; verification will confirm the order is paid with no new charge.", action="reprocess_stored_event")
            return _record(org_id, incident_id, trace, category="undetermined", obs="Processing failed but the reprocessing proposal was rejected by the guard checks.", exp="See the failed guard checks in the audit log: " + "; ".join(c["name"] for c in proposal.get("failed_checks", [])),
                           limits="Recovery is not safe under current evidence.", support=support, conflicting=conflicting, missing=[c["detail"] for c in proposal.get("failed_checks", [])], next_step="Escalate to an engineer.", action="escalate")
        return _record(org_id, incident_id, trace, category="undetermined", obs=f"The provider shows {ref} succeeded and the event was processed ({ev_hist[-1]['outcome'] if ev_hist else 'no processing record'}), yet the order is '{order['status']}'.",
                       exp="Application state contradicts its own processing history; this is not a case the supported recovery action addresses.", limits="Root cause unknown.", support=support, conflicting=conflicting + ["processing outcome vs order status"], missing=[],
                       next_step="Escalate to the on-call engineer with this evidence.", action="escalate")

    statuses = {p["status"] for p in provider.values()}
    if "requires_action" in statuses or "requires_confirmation" in statuses or "processing" in statuses:
        return _record(org_id, incident_id, trace, category="payment_incomplete_requires_action", obs="The customer reports a charge, but the provider shows the payment is not complete.",
                       exp="The provider record is waiting for customer authentication or confirmation; no funds were captured and no success event exists. The order correctly remains unpaid." + env_note,
                       limits="Whether the customer saw a pending authorization on their statement cannot be determined from this evidence.", support=support, conflicting=conflicting, missing=[],
                       next_step="Ask the customer to complete authentication or retry; do not reprocess or re-charge.", action="none")
    if statuses & {"requires_payment_method", "canceled"}:
        return _record(org_id, incident_id, trace, category="payment_failed", obs="The provider shows the payment did not succeed.", exp="The provider declined or canceled the payment; no success event exists and the order is correctly unpaid." + env_note,
                       limits="Decline reasons are not stored here.", support=support, conflicting=conflicting, missing=[], next_step="Invite the customer to retry with another payment method.", action="none")
    return _record(org_id, incident_id, trace, category="undetermined", obs="The evidence does not match a known pattern.", exp=f"Provider statuses: {sorted(statuses)}; order status {order['status']}.", limits="Insufficient evidence.",
                   support=support, conflicting=conflicting, missing=[], next_step="Escalate for manual review.", action="escalate")


def _propose(org_id: str, incident_id: str, event_row_id: str, rationale: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
    with tx() as conn:
        res = proposal_service.propose_reprocessing(conn, org_id, incident_id, event_row_id, rationale)
    trace.append({"type": "tool", "name": "propose_reprocessing", "args": [event_row_id], "result": {k: v for k, v in res.items() if k != "checks"}})
    return res


def _record(org_id: str, incident_id: str, trace: list[dict[str, Any]], *, category: str, obs: str, exp: str, limits: str, support: list[str], conflicting: list[str], missing: list[str], next_step: str, action: str) -> dict[str, Any]:
    payload = dict(category=category, observed_inconsistency=obs, explanation=exp, limits=limits, supporting_evidence_ids=list(dict.fromkeys(support)), conflicting_evidence=conflicting, missing_evidence=missing, next_step=next_step, recommended_action=action)
    with tx() as conn:
        res = diagnosis_service.record(conn, org_id, incident_id, payload, produced_by="deterministic", model="rules", prompt_version="rules-v1")
    trace.append({"type": "tool", "name": "record_diagnosis", "args": [category, action], "result": res})
    return res
