import { useEffect, useState } from "react";
import type { Api, DevUser, IncidentDetail } from "../lib/api";
import { EnvChip, Status, fmt, money } from "./common";

export function IncidentView({ api, org, id, user, onChanged }: { api: Api; org: string; id: string; user: DevUser; onChanged: () => void }) {
  const [d, setD] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const [trace, setTrace] = useState<Record<string, any>[] | null>(null);

  async function load() {
    try { setD(await api.getIncident(org, id)); setError(null); } catch (err) { setError((err as Error).message); }
  }
  useEffect(() => { setReport(null); setTrace(null); load(); const t = setInterval(load, 2000); return () => clearInterval(t); /* eslint-disable-line */ }, [org, id]);

  async function act(fn: () => Promise<unknown>) {
    try { await fn(); await load(); onChanged(); } catch (err) { setError((err as Error).message); }
  }

  if (error && !d) return <div className="panel err">{error}</div>;
  if (!d) return <div className="panel muted">Loading…</div>;
  const inc = d.incident;
  const dx = d.diagnoses[0];
  const active = d.proposals.find((p) => p.status === "proposed");
  const canApprove = user.role === "approver" || user.role === "admin";
  const evidenceById = Object.fromEntries(d.evidence.map((e) => [e.id, e]));
  const busy = ["investigating", "recovering", "verifying"].includes(inc.status);

  return (
    <div className="stack">
      {error && <div className="banner">{error}</div>}
      <div className="panel">
        <div className="queue-item" style={{ border: 0, padding: 0, cursor: "default" }}>
          <div className="row">
            <div><strong>{d.order?.external_ref ?? "Unlinked report"}</strong> <span className="muted small mono">{inc.id}</span></div>
            <Status value={inc.status} />
          </div>
        </div>
        <dl className="kv" style={{ marginTop: 10 }}>
          <dt>Reported via</dt><dd>{inc.source_type.replace("_", " ")}: {inc.source_value}</dd>
          {inc.complaint_text && <><dt>Customer report</dt><dd className="small"><span className="chip warn">untrusted</span> {inc.complaint_text}</dd></>}
          {d.order && <><dt>Order</dt><dd>{money(d.order.amount_minor, d.order.currency)} · <Status value={d.order.status} /> · v{d.order.version} · customer saw: {d.order.checkout_response ?? "—"}</dd></>}
          <dt>Assignee</dt>
          <dd>
            <select value={inc.assigned_to ?? ""} onChange={(e) => act(() => api.assign(org, id, e.target.value))} style={{ width: "auto" }}>
              <option value="" disabled>unassigned</option>
              {d.members.map((m) => <option key={m.user_id} value={m.user_id}>{m.display_name} ({m.role})</option>)}
            </select>
          </dd>
        </dl>
        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <button disabled={busy} onClick={() => act(() => api.investigate(org, id))}>Re-run investigation</button>
          <button disabled={busy || inc.status === "resolved"} onClick={() => act(() => api.escalate(org, id, "operator escalation"))}>Escalate</button>
          <button disabled={!["needs_review", "escalated", "awaiting_evidence"].includes(inc.status)} onClick={() => act(() => api.close(org, id, "explained; no recovery required"))}>Close as explained</button>
          <button onClick={async () => setReport((await api.report(org, id)).markdown)}>Report</button>
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <h2>Diagnosis</h2>
          {!dx && <div className="muted small">{busy ? "Investigator running…" : "No diagnosis yet."}</div>}
          {dx && (
            <div className="stack small">
              <div><span className="chip info">{dx.category.replace(/_/g, " ")}</span> <span className="chip">{dx.recommended_action.replace(/_/g, " ")}</span> <span className="muted">by {dx.produced_by} · {dx.model_version} · prompt {dx.prompt_version}</span></div>
              <div><strong>Observed inconsistency.</strong> {dx.observed_inconsistency}</div>
              <div><strong>Explanation.</strong> {dx.explanation}</div>
              <div><strong>Limits.</strong> {dx.limits}</div>
              <div><strong>Next safe step.</strong> {dx.next_step}</div>
              {dx.conflicting_evidence.length > 0 && <div><strong>Conflicting.</strong> {dx.conflicting_evidence.join("; ")}</div>}
              {dx.missing_evidence.length > 0 && <div><strong>Missing.</strong> {dx.missing_evidence.join("; ")}</div>}
              <details>
                <summary>{dx.supporting_evidence_ids.length} supporting evidence references</summary>
                {dx.supporting_evidence_ids.map((eid) => { const e = evidenceById[eid]; return <div key={eid} className="evidence-item">{e ? <><EnvChip env={e.environment} /> {e.label} <span className="muted">· {e.source} · event {fmt(e.event_time)} · ingested {fmt(e.ingested_at)}</span></> : <span className="err">unknown evidence {eid}</span>}</div>; })}
              </details>
            </div>
          )}
        </div>

        <div className="panel">
          <h2>Recovery</h2>
          {d.proposals.length === 0 && <div className="muted small">No recovery proposal. {dx && dx.recommended_action !== "reprocess_stored_event" ? "The investigator did not propose an action." : ""}</div>}
          {d.proposals.map((p) => (
            <div key={p.id} className="stack small" style={{ marginBottom: 12 }}>
              <div><Status value={p.status} /> <strong>{p.action_type.replace(/_/g, " ")}</strong> of <span className="mono">{p.provider_event_id}</span> ({p.event_type}) at order v{p.order_version} · expires {fmt(p.expires_at)}</div>
              <div>{p.rationale}</div>
              <details><summary>{p.guard_checks.length} guard checks</summary>
                <ul className="checks">{p.guard_checks.map((c) => <li key={c.name}><span className={`chip ${c.passed ? "ok" : "bad"}`}>{c.passed ? "pass" : "fail"}</span> {c.name}: <span className="muted">{c.detail}</span></li>)}</ul>
              </details>
              {p.status === "proposed" && (
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="primary" disabled={!canApprove} title={canApprove ? "" : "approver or admin role required"} onClick={() => act(() => api.approve(org, p.id))}>Approve reprocessing</button>
                  <button className="danger" onClick={() => act(() => api.reject(org, p.id, "operator rejected"))}>Reject</button>
                  {!canApprove && <span className="muted">Your role ({user.role}) cannot approve.</span>}
                </div>
              )}
            </div>
          ))}
          {d.recovery_jobs.map((j) => <div key={j.id} className="small">Job <span className="mono">{j.id.slice(0, 8)}</span> <Status value={j.status} /> attempts {j.attempt_count} {j.result?.reason && <span className="muted">· {j.result.reason}</span>}</div>)}
          {d.verification_results.map((v) => (
            <details key={v.id} open={!v.passed} style={{ marginTop: 8 }}>
              <summary>Verification {v.passed ? "passed" : "did not pass"} ({fmt(v.created_at)})</summary>
              <ul className="checks small">{v.checks.map((c) => <li key={c.name}><span className={`chip ${c.passed === true ? "ok" : c.passed === null ? "warn" : "bad"}`}>{c.passed === true ? "pass" : c.passed === null ? "missing" : "fail"}</span> {c.name}: <span className="muted">{c.detail}</span></li>)}</ul>
            </details>
          ))}
          {active && !canApprove && <div className="banner small" style={{ marginTop: 8 }}>Awaiting an approver.</div>}
        </div>
      </div>

      <div className="panel">
        <h2>Timeline (event time · ingestion time)</h2>
        <ul className="timeline small">
          {d.timeline.map((t, i) => (
            <li key={i}>
              <span className="mono">{fmt(t.t)}{t.ingested && t.ingested !== t.t ? <span className="muted"> → {fmt(t.ingested)}</span> : null}</span>
              <span><span className="chip">{t.kind}</span></span>
              <span><strong>{t.title}</strong> {t.untrusted && <span className="chip warn">untrusted</span>} <span className="muted">{t.detail}</span> {t.source !== "application" && t.source !== "workspace" && <EnvChip env={t.source} />}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="grid2">
        <div className="panel">
          <h2>Evidence ({d.evidence.length})</h2>
          <div className="small" style={{ maxHeight: 300, overflow: "auto" }}>
            {d.evidence.map((e) => <div key={e.id} className="evidence-item"><EnvChip env={e.environment} /> {e.label} <span className="muted">· {e.source} · {e.source_id}</span></div>)}
          </div>
        </div>
        <div className="panel">
          <h2>Investigator runs</h2>
          {d.agent_runs.map((r) => (
            <div key={r.id} className="small" style={{ marginBottom: 6 }}>
              <Status value={r.status} /> {r.mode} · {r.model} · {r.tool_calls} tool calls · {r.latency_ms} ms {r.input_tokens ? `· ${r.input_tokens}/${r.output_tokens} tokens` : ""} {r.error && <span className="err">· {r.error}</span>}
              <button style={{ marginLeft: 8 }} onClick={async () => setTrace((await api.runTrace(org, id, r.id)).trace)}>trace</button>
            </div>
          ))}
          {trace && <pre className="small">{JSON.stringify(trace, null, 2)}</pre>}
          <h2 style={{ marginTop: 12 }}>Audit</h2>
          <div className="small" style={{ maxHeight: 200, overflow: "auto" }}>{d.audit.map((a) => <div key={a.id}><span className="mono muted">{fmt(a.created_at)}</span> {a.actor_type}:{a.actor_id.slice(0, 12)} · {a.action.replace(/_/g, " ")}</div>)}</div>
        </div>
      </div>
      {report && <div className="panel"><h2>Incident report</h2><pre>{report}</pre></div>}
    </div>
  );
}
