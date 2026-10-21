export function fmt(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleString(undefined, { hour12: false, month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function money(minor: number | string | undefined, currency: string | undefined): string {
  if (minor === undefined || minor === null) return "—";
  return `${(Number(minor) / 100).toFixed(2)} ${(currency ?? "").toUpperCase()}`;
}

const TONE: Record<string, string> = {
  resolved: "ok", recovering: "info", verifying: "info", investigating: "info", awaiting_approval: "warn", awaiting_evidence: "warn",
  needs_review: "warn", escalated: "bad", failed: "bad", open: "", paid: "ok", pending: "warn", requires_action: "warn", needs_review_order: "bad",
  succeeded: "ok", applied: "ok", duplicate: "info", rejected: "bad", superseded: "", executed: "ok", proposed: "warn", approved: "info", queued: "info", running: "info",
};

export function Status({ value }: { value: string }) {
  return <span className={`chip ${TONE[value] ?? ""}`}>{value.replace(/_/g, " ")}</span>;
}

export function EnvChip({ env }: { env: string }) {
  const label = env === "sandbox" ? "sandbox · not settlement" : env === "simulated" ? "simulated · not settlement" : env;
  return <span className={`chip ${env === "sandbox" || env === "simulated" ? "warn" : ""}`}>{label}</span>;
}
