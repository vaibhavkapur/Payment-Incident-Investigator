"""Diagnosis persistence with server-side validation of evidence references."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import Connection

from ..db import execute, fetch_one
from . import audit, evidence

CATEGORIES = [
    "processing_failure_after_receipt",   # provider succeeded, verified event stored, handler failed -> reprocess
    "lost_checkout_response_recovered",   # provider succeeded, customer saw an error, order later repaired by webhook
    "late_webhook_applied",               # customer saw failure/pending, webhook later marked the order paid
    "duplicate_webhook_deduplicated",     # same event delivered more than once, one effect
    "webhook_not_received",               # provider succeeded, no verified event was ever stored
    "payment_incomplete_requires_action", # provider waiting for customer authentication
    "payment_failed",                     # provider declined; nothing to repair
    "multiple_payment_attempts",          # more than one successful payment for one order -> escalate
    "provider_evidence_unavailable",      # provider lookup failed; cannot conclude
    "unresolved_identifier",              # could not link the report to an order
    "no_inconsistency",                   # application and provider agree
    "undetermined",                       # evidence is contradictory or insufficient -> escalate
]
ACTIONS = ["reprocess_stored_event", "request_information", "escalate", "none"]


class DiagnosisInput(BaseModel):
    category: str
    observed_inconsistency: str = Field(min_length=1, max_length=2000)
    explanation: str = Field(min_length=1, max_length=4000)
    limits: str = Field(min_length=1, max_length=2000)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    next_step: str = Field(min_length=1, max_length=2000)
    recommended_action: str

    @field_validator("category")
    @classmethod
    def _cat(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}")
        return v

    @field_validator("recommended_action")
    @classmethod
    def _act(cls, v: str) -> str:
        if v not in ACTIONS:
            raise ValueError(f"recommended_action must be one of {ACTIONS}")
        return v


def record(conn: Connection, org_id: str, incident_id: str, payload: dict[str, Any], *, produced_by: str, model: str | None, prompt_version: str | None) -> dict[str, Any]:
    try:
        d = DiagnosisInput.model_validate(payload)
    except ValidationError as exc:
        return {"recorded": False, "error": exc.errors(include_url=False)}
    known = evidence.ids_for_incident(conn, org_id, incident_id)
    unknown = [e for e in d.supporting_evidence_ids if e not in known]
    if unknown:
        return {"recorded": False, "error": f"unknown evidence ids {unknown}; cite only evidence_ids returned by tools in this incident", "valid_evidence_ids": sorted(known)}
    if not d.supporting_evidence_ids and d.category not in ("unresolved_identifier", "provider_evidence_unavailable"):
        return {"recorded": False, "error": "a diagnosis must cite at least one supporting evidence id"}
    if d.recommended_action == "reprocess_stored_event":
        active = fetch_one(conn, "select id from action_proposals where org_id = :org and incident_id = :inc and status in ('proposed','approved')", org=org_id, inc=incident_id)
        if not active:
            return {"recorded": False, "error": "recommended_action=reprocess_stored_event requires an accepted proposal; call propose_reprocessing first, or choose another action"}
    row = fetch_one(
        conn,
        """insert into diagnoses (org_id, incident_id, category, observed_inconsistency, explanation, limits, supporting_evidence_ids, conflicting_evidence, missing_evidence, next_step, recommended_action, produced_by, model_version, prompt_version)
           values (:org, :inc, :cat, :obs, :exp, :lim, cast(:sup as uuid[]), cast(:conf as text[]), cast(:miss as text[]), :next, :act, :by, :model, :pv) returning id""",
        org=org_id, inc=incident_id, cat=d.category, obs=d.observed_inconsistency, exp=d.explanation, lim=d.limits, sup=d.supporting_evidence_ids, conf=d.conflicting_evidence, miss=d.missing_evidence,
        next=d.next_step, act=d.recommended_action, by=produced_by, model=model, pv=prompt_version,
    )
    status = {"reprocess_stored_event": "awaiting_approval", "request_information": "awaiting_evidence", "escalate": "escalated", "none": "needs_review"}[d.recommended_action]
    execute(conn, "update incidents set status = :st, summary = :summary, updated_at = now() where org_id = :org and id = :id", st=status, summary=f"{d.category}: {d.observed_inconsistency[:300]}", org=org_id, id=incident_id)
    audit.record(conn, org_id, incident_id, "agent", produced_by, "diagnosis_recorded", {"diagnosis_id": row["id"], "category": d.category, "recommended_action": d.recommended_action, "incident_status": status})
    return {"recorded": True, "diagnosis_id": row["id"], "incident_status": status}
