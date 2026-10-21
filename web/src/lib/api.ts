export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8100";

export interface DevUser { user_id: string; display_name: string; role: string; org_name: string; org_id: string; }
export interface Incident {
  id: string; org_id: string; status: string; source_type: string; source_value: string; complaint_text: string | null;
  order_id: string | null; summary: string | null; assigned_to: string | null; created_at: string; updated_at: string;
  external_ref?: string; amount_minor?: number; currency?: string; order_status?: string; assignee_name?: string;
}
export interface Evidence { id: string; source: string; source_id: string; environment: string; label: string; fields: Record<string, unknown>; event_time: string | null; ingested_at: string | null; retrieved_at: string; }
export interface Diagnosis { id: string; category: string; observed_inconsistency: string; explanation: string; limits: string; supporting_evidence_ids: string[]; conflicting_evidence: string[]; missing_evidence: string[]; next_step: string; recommended_action: string; produced_by: string; model_version: string; prompt_version: string; created_at: string; }
export interface GuardCheck { name: string; passed: boolean | null; detail: string; }
export interface Proposal { id: string; action_type: string; provider_event_id: string; event_type: string; provider_event_row_id: string; order_version: number; rationale: string; guard_checks: GuardCheck[]; status: string; expires_at: string; created_at: string; }
export interface TimelineItem { t: string; ingested?: string; kind: string; title: string; detail: string; source: string; untrusted?: boolean; }
export interface IncidentDetail {
  incident: Incident; order: Record<string, any> | null; payment_attempts: Record<string, any>[]; provider_events: Record<string, any>[];
  processing_attempts: Record<string, any>[]; evidence: Evidence[]; diagnoses: Diagnosis[]; proposals: Proposal[]; approvals: Record<string, any>[];
  recovery_jobs: Record<string, any>[]; verification_results: { id: string; passed: boolean; checks: GuardCheck[]; created_at: string }[];
  audit: Record<string, any>[]; agent_runs: Record<string, any>[]; members: { user_id: string; role: string; display_name: string }[]; timeline: TimelineItem[];
}

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }

export function client(userId: string | null) {
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${API_URL}${path}`, {
      method,
      headers: { "content-type": "application/json", ...(userId ? { "X-User-Id": userId } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
      throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json() as Promise<T>;
  }
  return {
    devUsers: () => call<DevUser[]>("GET", "/dev/users"),
    health: () => call<{ ok: boolean; agent_mode: string; auth_mode: string }>("GET", "/health"),
    listIncidents: (org: string) => call<Incident[]>("GET", `/orgs/${org}/incidents`),
    getIncident: (org: string, id: string) => call<IncidentDetail>("GET", `/orgs/${org}/incidents/${id}`),
    createIncident: (org: string, body: { source_type: string; source_value: string; complaint_text?: string }) => call<Incident>("POST", `/orgs/${org}/incidents`, body),
    investigate: (org: string, id: string) => call("POST", `/orgs/${org}/incidents/${id}/investigate`),
    assign: (org: string, id: string, user_id: string) => call("POST", `/orgs/${org}/incidents/${id}/assign`, { user_id }),
    escalate: (org: string, id: string, reason: string) => call("POST", `/orgs/${org}/incidents/${id}/escalate`, { reason }),
    close: (org: string, id: string, reason: string) => call("POST", `/orgs/${org}/incidents/${id}/close`, { reason }),
    report: (org: string, id: string) => call<{ markdown: string }>("GET", `/orgs/${org}/incidents/${id}/report`),
    runTrace: (org: string, id: string, runId: string) => call<{ trace: Record<string, any>[] }>("GET", `/orgs/${org}/incidents/${id}/runs/${runId}`),
    approve: (org: string, pid: string) => call("POST", `/orgs/${org}/proposals/${pid}/approve`, {}),
    reject: (org: string, pid: string, reason: string) => call("POST", `/orgs/${org}/proposals/${pid}/reject`, { reason }),
  };
}
export type Api = ReturnType<typeof client>;
