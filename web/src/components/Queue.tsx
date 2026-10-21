import { useState } from "react";
import type { Api, Incident } from "../lib/api";
import { Status, fmt, money } from "./common";

export function Queue({ api, org, incidents, selected, onSelect, onCreated }: { api: Api; org: string; incidents: Incident[]; selected: string | null; onSelect: (id: string) => void; onCreated: () => void }) {
  const [sourceType, setSourceType] = useState("complaint");
  const [sourceValue, setSourceValue] = useState("");
  const [complaint, setComplaint] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const inc = await api.createIncident(org, { source_type: sourceType, source_value: sourceValue || complaint.slice(0, 120) || "customer report", complaint_text: complaint || undefined });
      setSourceValue(""); setComplaint("");
      onCreated(); onSelect(inc.id);
    } catch (err) { setError(String((err as Error).message)); } finally { setBusy(false); }
  }

  return (
    <div className="stack">
      <div className="panel">
        <h2>New incident</h2>
        <form className="form" onSubmit={submit}>
          <select value={sourceType} onChange={(e) => setSourceType(e.target.value)}>
            <option value="complaint">Customer complaint</option>
            <option value="order_id">Order ID / reference</option>
            <option value="payment_ref">Provider payment reference</option>
          </select>
          <input placeholder={sourceType === "complaint" ? "Short title (optional)" : sourceType === "order_id" ? "Order UUID or ORD-…" : "pi_…"} value={sourceValue} onChange={(e) => setSourceValue(e.target.value)} required={sourceType !== "complaint"} />
          {sourceType === "complaint" && <textarea rows={3} placeholder="Paste the customer's message. It is treated as untrusted evidence." value={complaint} onChange={(e) => setComplaint(e.target.value)} required />}
          {error && <div className="err small">{error}</div>}
          <button className="primary" disabled={busy}>{busy ? "Creating…" : "Create and investigate"}</button>
        </form>
      </div>
      <div className="panel">
        <h2>Incident queue ({incidents.length})</h2>
        {incidents.length === 0 && <div className="muted small">No incidents yet.</div>}
        {incidents.map((i) => (
          <div key={i.id} className={`queue-item ${selected === i.id ? "active" : ""}`} onClick={() => onSelect(i.id)}>
            <div className="row"><strong>{i.external_ref ?? i.source_value.slice(0, 28)}</strong><Status value={i.status} /></div>
            <div className="row small muted"><span>{i.summary ? i.summary.split(":")[0].replace(/_/g, " ") : i.source_type.replace("_", " ")}</span><span>{money(i.amount_minor, i.currency)}</span></div>
            <div className="row small muted"><span>{i.assignee_name ?? "unassigned"}</span><span>{fmt(i.created_at)}</span></div>
          </div>
        ))}
      </div>
    </div>
  );
}
