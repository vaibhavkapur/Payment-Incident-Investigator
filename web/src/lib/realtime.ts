import { useEffect, useRef } from "react";

/**
 * Live updates. With VITE_SUPABASE_URL/ANON_KEY set, subscribes to Supabase Realtime changes
 * on the incident tables (RLS restricts rows to the user's organizations); otherwise polls.
 */
export function useLiveRefresh(refresh: () => void, deps: unknown[], intervalMs = 2000) {
  const latest = useRef(refresh);
  latest.current = refresh;
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    let channel: { unsubscribe: () => void } | undefined;
    let cancelled = false;
    const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
    const key = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;
    latest.current();
    if (url && key) {
      import("@supabase/supabase-js").then(({ createClient }) => {
        if (cancelled) return;
        const supabase = createClient(url, key);
        channel = supabase
          .channel("incident-workspace")
          .on("postgres_changes", { event: "*", schema: "public", table: "incidents" }, () => latest.current())
          .on("postgres_changes", { event: "*", schema: "public", table: "recovery_jobs" }, () => latest.current())
          .on("postgres_changes", { event: "*", schema: "public", table: "action_proposals" }, () => latest.current())
          .subscribe();
      });
      timer = setInterval(() => latest.current(), intervalMs * 5);
    } else {
      timer = setInterval(() => latest.current(), intervalMs);
    }
    return () => { cancelled = true; if (timer) clearInterval(timer); channel?.unsubscribe(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
