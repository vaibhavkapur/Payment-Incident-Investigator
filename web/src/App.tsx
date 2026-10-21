import { useCallback, useEffect, useMemo, useState } from "react";
import { client, type DevUser, type Incident } from "./lib/api";
import { useLiveRefresh } from "./lib/realtime";
import { Queue } from "./components/Queue";
import { IncidentView } from "./components/IncidentView";

export default function App() {
  const [users, setUsers] = useState<DevUser[]>([]);
  const [userId, setUserId] = useState<string | null>(() => { try { return localStorage.getItem("pii.user"); } catch { return null; } });
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [health, setHealth] = useState<{ agent_mode: string; auth_mode: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const api = useMemo(() => client(userId), [userId]);
  const user = users.find((u) => u.user_id === userId) ?? null;

  useEffect(() => {
    client(null).devUsers().then((u) => { setUsers(u); if (!userId && u[0]) setUserId(u[0].user_id); }).catch((e) => setError(`API unreachable: ${e.message}`));
    client(null).health().then(setHealth).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refresh = useCallback(() => {
    if (!user) return;
    api.listIncidents(user.org_id).then((list) => { setIncidents(list); setError(null); }).catch((e) => setError(e.message));
  }, [api, user]);
  useLiveRefresh(refresh, [user?.org_id, api]);

  function switchUser(id: string) { setUserId(id); setSelected(null); try { localStorage.setItem("pii.user", id); } catch { /* ignore */ } }

  return (
    <>
      <header>
        <h1>Payment Incident Investigator</h1>
        {health && <span className="chip small">{health.agent_mode === "adk" ? "Google ADK investigator" : "rule-based investigator (no Gemini key)"}</span>}
        <span className="spacer" />
        {error && <span className="err small">{error}</span>}
        <label className="small muted">Operator&nbsp;
          <select value={userId ?? ""} onChange={(e) => switchUser(e.target.value)} style={{ width: "auto" }}>
            {users.map((u) => <option key={u.user_id} value={u.user_id}>{u.display_name} · {u.org_name}</option>)}
          </select>
        </label>
      </header>
      {user && (
        <div className="layout">
          <Queue api={api} org={user.org_id} incidents={incidents} selected={selected} onSelect={setSelected} onCreated={refresh} />
          {selected ? <IncidentView api={api} org={user.org_id} id={selected} user={user} onChanged={refresh} /> : <div className="panel muted">Select an incident or create one. Organization: <strong>{user.org_name}</strong>. Your role: {user.role}.</div>}
        </div>
      )}
    </>
  );
}
