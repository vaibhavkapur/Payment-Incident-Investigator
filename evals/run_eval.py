"""Evaluation runner.

    python evals/run_eval.py --set dev --repeat 1
    python evals/run_eval.py --set heldout --seed 7 --repeat 3

Builds scenarios through the merchant, opens incidents through the API as a normal analyst,
waits for the investigator, and scores each run against ground truth that only this process
holds. Writes a Markdown report and a JSON file under evals/reports/.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals import scenarios as S  # noqa: E402

API = os.environ.get("API_URL", "http://localhost:8100")
MERCHANT = os.environ.get("MERCHANT_URL", "http://localhost:4100")
ANALYST = "aaaaaaaa-0000-0000-0000-000000000001"
SETTLEMENT_WORDS = ("settled", "settlement confirmed", "funds received", "money has arrived", "bank confirmed")


def wait_for_diagnosis(api: httpx.Client, org: str, incident_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = api.get(f"/orgs/{org}/incidents/{incident_id}", headers={"X-User-Id": ANALYST}).json()
        runs = d["agent_runs"]
        if d["diagnoses"] or (runs and runs[0]["status"] not in ("running",)):
            return d
        time.sleep(0.5)
    return api.get(f"/orgs/{org}/incidents/{incident_id}", headers={"X-User-Id": ANALYST}).json()


def score(sc: S.Scenario, d: dict) -> dict:
    dx = d["diagnoses"][0] if d["diagnoses"] else None
    evidence = {e["id"]: e for e in d["evidence"]}
    run = d["agent_runs"][0] if d["agent_runs"] else {}
    proposals = [p for p in d["proposals"] if p["status"] not in ("rejected",)]
    out = {
        "scenario": sc.name, "source": sc.source, "truth_category": sc.truth.category, "truth_action": sc.truth.action,
        "category": dx["category"] if dx else None, "action": dx["recommended_action"] if dx else None,
        "produced_by": dx["produced_by"] if dx else None, "model": run.get("model"), "prompt_version": run.get("prompt_version"),
        "tool_calls": run.get("tool_calls"), "latency_ms": run.get("latency_ms"), "input_tokens": run.get("input_tokens"), "output_tokens": run.get("output_tokens"),
        "run_status": run.get("status"), "incident_status": d["incident"]["status"], "notes": sc.notes, "distractors": sc.distractors,
    }
    out["diagnosis_correct"] = bool(dx) and dx["category"] == sc.truth.category
    out["action_correct"] = bool(dx) and dx["recommended_action"] == sc.truth.action
    refs = dx["supporting_evidence_ids"] if dx else []
    out["evidence_refs_valid"] = bool(dx) and all(r in evidence for r in refs) and (len(refs) > 0 or sc.truth.category in ("unresolved_identifier", "provider_evidence_unavailable"))
    out["proposal_correct"] = (len(proposals) > 0) == sc.truth.proposal_expected
    out["escalation_correct"] = ((dx or {}).get("recommended_action") == "escalate") == (sc.truth.action == "escalate")
    out["incorrect_recovery_proposal"] = (len(proposals) > 0) and not sc.truth.proposal_expected
    # Unsupported claims: provider success asserted without a cited provider record showing success,
    # or settlement language anywhere in the diagnosis.
    unsupported = []
    if dx:
        text = " ".join([dx["explanation"], dx["observed_inconsistency"], dx["limits"]]).lower()
        if any(w in text for w in SETTLEMENT_WORDS):
            unsupported.append("settlement language")
        cited_provider_success = any(evidence[r]["source"] == "provider" and evidence[r]["fields"].get("status") == "succeeded" for r in refs if r in evidence)
        claims_success = dx["category"] in ("processing_failure_after_receipt", "lost_checkout_response_recovered", "late_webhook_applied", "duplicate_webhook_deduplicated", "webhook_not_received", "multiple_payment_attempts", "no_inconsistency")
        if claims_success and not cited_provider_success:
            unsupported.append("provider success asserted without a cited provider record")
        if dx["category"] == "processing_failure_after_receipt" and not any(evidence[r]["source"] == "processing_history" and evidence[r]["fields"].get("outcome") == "failed" for r in refs if r in evidence):
            unsupported.append("processing failure asserted without a cited failed processing record")
    out["unsupported_claims"] = unsupported
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["dev", "heldout"], default="dev")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=240)
    ap.add_argument("--distractors", action="store_true", help="add same-amount orders in both organizations around each scenario")
    args = ap.parse_args()

    m = S.Merchant(MERCHANT, httpx.get(f"{MERCHANT}/health").json()["provider_mode"])
    api = httpx.Client(base_url=API, timeout=60)
    health = api.get("/health").json()
    rng = random.Random(args.seed)
    specs = S.DEV_SET if args.set == "dev" else S.held_out_set(args.seed)
    if m.provider_mode != "simulated":
        specs = [s for s in specs if s[0] != "provider_unavailable"]

    rows = []
    for rep in range(args.repeat):
        for family, params in specs:
            builder = S.FAMILIES[family]
            try:
                sc = builder(m, rng, dict(params))
                if args.distractors:
                    S.add_distractors(m, rng, sc, int(params.get("amount") or 4999), params.get("currency", "usd"))
            except Exception as exc:  # noqa: BLE001
                rows.append({"scenario": family, "error": f"scenario build failed: {exc}"})
                continue
            r = api.post(f"/orgs/{sc.org_id}/incidents", json=sc.report, headers={"X-User-Id": ANALYST})
            r.raise_for_status()
            d = wait_for_diagnosis(api, sc.org_id, r.json()["id"], args.timeout)
            row = score(sc, d)
            row["incident_id"] = r.json()["id"]
            row["repeat"] = rep
            rows.append(row)
            print(f"[{rep}] {sc.name:22s} -> {row['category']!s:36s} {'OK ' if row['diagnosis_correct'] else 'MISS'} action={'OK' if row['action_correct'] else 'MISS'} refs={'OK' if row['evidence_refs_valid'] else 'BAD'} tools={row['tool_calls']} {row['latency_ms']}ms")

    scored = [r for r in rows if "error" not in r]
    def rate(key: str) -> str:
        return f"{sum(1 for r in scored if r[key]) / len(scored) * 100:.0f}%" if scored else "n/a"
    lat = [r["latency_ms"] for r in scored if r.get("latency_ms") is not None]
    tokens_in = sum(r.get("input_tokens") or 0 for r in scored)
    tokens_out = sum(r.get("output_tokens") or 0 for r in scored)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_dir = Path(__file__).resolve().parent / "reports"
    report_dir.mkdir(exist_ok=True)
    lines = [
        f"# Evaluation report — {args.set} set", "",
        f"Generated {ts}. Agent mode `{health.get('agent_mode')}`, provider mode `{m.provider_mode}`, seed {args.seed}, repeats {args.repeat}.",
        f"Model/prompt: {sorted({(r.get('model'), r.get('prompt_version')) for r in scored})}", "",
        "| Metric | Value |", "|---|---|",
        f"| Scenarios scored | {len(scored)} (build errors: {len(rows) - len(scored)}) |",
        f"| Diagnosis accuracy | {rate('diagnosis_correct')} |",
        f"| Recommended action correct | {rate('action_correct')} |",
        f"| Evidence references valid | {rate('evidence_refs_valid')} |",
        f"| Runs with unsupported claims | {sum(1 for r in scored if r['unsupported_claims'])} |",
        f"| Appropriate escalation | {rate('escalation_correct')} |",
        f"| Incorrect recovery proposals | {sum(1 for r in scored if r['incorrect_recovery_proposal'])} |",
        f"| Tool calls (mean / max) | {statistics.mean(r['tool_calls'] for r in scored if r.get('tool_calls') is not None):.1f} / {max((r['tool_calls'] or 0) for r in scored)} |" if scored else "| Tool calls | n/a |",
        f"| Latency ms (median / p95) | {statistics.median(lat):.0f} / {sorted(lat)[int(len(lat) * 0.95) - 1 if len(lat) > 1 else 0]:.0f} |" if lat else "| Latency | n/a |",
        f"| Tokens in / out (total) | {tokens_in} / {tokens_out} |",
        "", "## Per scenario", "", "| # | Scenario | Source | Expected | Got | Action | Refs | Proposal | Unsupported | Tools | ms | Notes |", "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        if "error" in r:
            lines.append(f"| {i} | {r['scenario']} | | | ERROR | | | | {r['error']} | | | |")
            continue
        lines.append(f"| {i} | {r['scenario']} | {r['source']} | {r['truth_category']} | {r['category']} {'✓' if r['diagnosis_correct'] else '✗'} | {r['action']} {'✓' if r['action_correct'] else '✗'} | {'✓' if r['evidence_refs_valid'] else '✗'} | {'✓' if r['proposal_correct'] else '✗'} | {', '.join(r['unsupported_claims']) or '—'} | {r['tool_calls']} | {r['latency_ms']} | {'; '.join(r['notes'])}{' · distractors: ' + str(len(r['distractors'])) if r['distractors'] else ''} |")
    lines += ["", "Scenario source `simulated` = local Stripe-shaped simulator with labeled fault injection; `sandbox` = real Stripe sandbox. Neither is real settlement.", ""]
    md = report_dir / f"{ts}-{args.set}.md"
    md.write_text("\n".join(lines))
    (report_dir / f"{ts}-{args.set}.json").write_text(json.dumps(rows, indent=2, default=str))
    print(f"\nreport: {md}")
    print("\n".join(lines[4:16]))
    return 0 if scored and all(r["diagnosis_correct"] and r["evidence_refs_valid"] and not r["incorrect_recovery_proposal"] for r in scored) else 1


if __name__ == "__main__":
    raise SystemExit(main())
