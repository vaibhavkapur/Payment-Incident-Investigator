import { pool, type Queryable } from "./db.js";

export type FaultKind =
  | "handler_crash"      // stored event processing throws before the order is updated
  | "drop_webhook"       // simulator never delivers the webhook (or sandbox endpoint rejects it)
  | "duplicate_webhook"  // simulator delivers the same event twice
  | "delay_webhook"      // simulator delivers after params.delay_ms
  | "lost_response"      // checkout response is lost after the provider accepted the payment
  | "provider_unavailable"; // simulated provider lookup fails for this payment

export interface Fault {
  id: string;
  org_id: string;
  order_id: string | null;
  kind: FaultKind;
  params: Record<string, unknown>;
  remaining: number;
}

export async function setFault(orgId: string, orderId: string | null, kind: FaultKind, params: Record<string, unknown> = {}, remaining = 1): Promise<Fault> {
  const res = await pool.query(
    `insert into fault_injections (org_id, order_id, kind, params, remaining) values ($1, $2, $3, $4, $5) returning *`,
    [orgId, orderId, kind, JSON.stringify(params), remaining],
  );
  return res.rows[0] as Fault;
}

/** Peeks at an active fault without consuming it. */
export async function peekFault(orgId: string, orderId: string, kind: FaultKind, q: Queryable = pool): Promise<Fault | null> {
  const res = await q.query(
    `select * from fault_injections where org_id = $1 and order_id = $2 and kind = $3 and remaining > 0 order by created_at limit 1`,
    [orgId, orderId, kind],
  );
  return (res.rows[0] as Fault) ?? null;
}

/** Consumes one use of a matching fault and returns it, or null if none is active. */
export async function consumeFault(orgId: string, orderId: string, kind: FaultKind, q: Queryable = pool): Promise<Fault | null> {
  const res = await q.query(
    `update fault_injections set remaining = remaining - 1
     where id = (select id from fault_injections where org_id = $1 and order_id = $2 and kind = $3 and remaining > 0 order by created_at limit 1)
     returning *`,
    [orgId, orderId, kind],
  );
  return (res.rows[0] as Fault) ?? null;
}
