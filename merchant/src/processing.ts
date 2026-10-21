import type pg from "pg";
import { pool, withTx, one } from "./db.js";
import { appLog } from "./logger.js";
import { consumeFault } from "./faults.js";

export interface ProcessingResult {
  processing_attempt_id: string;
  outcome: "applied" | "duplicate" | "failed" | "skipped";
  business_op_key: string | null;
  order_id: string | null;
  order_version_before: number | null;
  order_version_after: number | null;
  error: string | null;
  already_recorded?: boolean;
}

interface EventRow {
  id: string;
  org_id: string;
  provider_event_id: string;
  event_type: string;
  provider_payment_ref: string | null;
  payload: { data?: { object?: { metadata?: { order_id?: string }; amount?: number; currency?: string } } };
}

interface OrderRow { id: string; status: string; version: number; amount_minor: string; currency: string; }

export interface ProcessInput {
  orgId: string;
  eventRowId: string;
  triggeredBy: "webhook" | "reprocess";
  recoveryJobId?: string | null;
  requestId?: string | null;
}

/**
 * The merchant's single event handler. Both the webhook collector and the recovery
 * worker's reprocess path call it with an already-stored event. Domain effects are
 * deduplicated by a durable business-operation key, so running it twice for the same
 * event (or for two deliveries of the same event) produces exactly one effect.
 */
export async function processStoredEvent(input: ProcessInput): Promise<ProcessingResult> {
  const { orgId, eventRowId, triggeredBy, recoveryJobId = null, requestId = null } = input;

  // Reprocess idempotency: one recovery job maps to at most one processing attempt.
  if (recoveryJobId) {
    const prior = await one<Omit<ProcessingResult, "outcome"> & { outcome: string }>(
      pool,
      `select id as processing_attempt_id, outcome, business_op_key, order_id, order_version_before, order_version_after, error
       from processing_attempts where org_id = $1 and recovery_job_id = $2`,
      [orgId, recoveryJobId],
    );
    if (prior && prior.outcome !== "running") return { ...(prior as ProcessingResult), already_recorded: true };
  }

  const event = await one<EventRow>(pool, `select * from provider_events where org_id = $1 and id = $2`, [orgId, eventRowId]);
  if (!event) throw new Error("event not found in organization");

  const order = await resolveOrder(pool, event);
  const attempt = await one<{ id: string }>(
    pool,
    `insert into processing_attempts (org_id, provider_event_row_id, order_id, triggered_by, recovery_job_id, outcome, order_version_before)
     values ($1, $2, $3, $4, $5, 'running', $6) returning id`,
    [orgId, event.id, order?.id ?? null, triggeredBy, recoveryJobId, order?.version ?? null],
  );
  const attemptId = attempt!.id;
  const base = { orgId, orderId: order?.id ?? null, providerPaymentRef: event.provider_payment_ref, providerEventId: event.provider_event_id, requestId };

  await appLog({ ...base, component: "event_processor", event: "processing.started", message: `Processing ${event.event_type} (${triggeredBy})`, fields: { processing_attempt_id: attemptId, triggered_by: triggeredBy, recovery_job_id: recoveryJobId } });

  const finish = async (outcome: ProcessingResult["outcome"], extra: Partial<ProcessingResult> = {}, error: string | null = null): Promise<ProcessingResult> => {
    await pool.query(
      `update processing_attempts set outcome = $2, error = $3, business_op_key = $4, order_version_after = $5, finished_at = now() where id = $1`,
      [attemptId, outcome, error, extra.business_op_key ?? null, extra.order_version_after ?? null],
    );
    return {
      processing_attempt_id: attemptId,
      outcome,
      business_op_key: extra.business_op_key ?? null,
      order_id: order?.id ?? null,
      order_version_before: order?.version ?? null,
      order_version_after: extra.order_version_after ?? null,
      error,
    };
  };

  if (!order) {
    await appLog({ ...base, level: "warn", component: "event_processor", event: "processing.skipped", message: "No order matches this event in the organization", fields: { processing_attempt_id: attemptId } });
    return finish("skipped", {}, "no matching order");
  }

  const plan = planEffect(event, order);
  if (!plan) {
    await appLog({ ...base, component: "event_processor", event: "processing.skipped", message: `No domain effect for ${event.event_type}`, fields: { processing_attempt_id: attemptId } });
    return finish("skipped", {}, `unhandled event type ${event.event_type}`);
  }

  // Local fault injection: the handler fails after receipt, before the order is updated.
  const crash = await consumeFault(orgId, order.id, "handler_crash");
  if (crash) {
    const message = String(crash.params.error ?? "OrderRepository.markPaid: connection terminated unexpectedly");
    await appLog({ ...base, level: "error", component: "event_processor", event: "processing.failed", message, fields: { processing_attempt_id: attemptId, business_op_key: plan.opKey, stack: "OrderRepository.markPaid (src/orders/repository.ts:88)\n    at processStoredEvent (src/processing.ts:112)" } });
    return finish("failed", { business_op_key: plan.opKey }, message);
  }

  try {
    const result = await withTx(async (tx) => {
      const inserted = await tx.query(
        `insert into business_operations (org_id, op_key, provider_event_row_id) values ($1, $2, $3) on conflict do nothing returning op_key`,
        [orgId, plan.opKey, event.id],
      );
      if (inserted.rowCount === 0) return { outcome: "duplicate" as const, versionAfter: order.version };
      const updated = await tx.query(
        `update orders set status = $3, version = version + 1, updated_at = now() where org_id = $1 and id = $2 returning version`,
        [orgId, order.id, plan.orderStatus],
      );
      if (plan.attemptStatus && event.provider_payment_ref) {
        await tx.query(`update payment_attempts set status = $3, updated_at = now() where org_id = $1 and provider_payment_ref = $2`, [orgId, event.provider_payment_ref, plan.attemptStatus]);
      }
      return { outcome: "applied" as const, versionAfter: Number(updated.rows[0].version) };
    });
    if (result.outcome === "duplicate") {
      await appLog({ ...base, component: "event_processor", event: "processing.duplicate", message: `Business operation already applied; no effect (${plan.opKey})`, fields: { processing_attempt_id: attemptId, business_op_key: plan.opKey } });
      return finish("duplicate", { business_op_key: plan.opKey, order_version_after: result.versionAfter });
    }
    await appLog({ ...base, component: "event_processor", event: "processing.applied", message: `Order ${order.id} -> ${plan.orderStatus}${plan.note ? ` (${plan.note})` : ""}`, fields: { processing_attempt_id: attemptId, business_op_key: plan.opKey, order_status: plan.orderStatus, order_version: result.versionAfter } });
    return finish("applied", { business_op_key: plan.opKey, order_version_after: result.versionAfter });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    await appLog({ ...base, level: "error", component: "event_processor", event: "processing.failed", message, fields: { processing_attempt_id: attemptId, business_op_key: plan.opKey } });
    return finish("failed", { business_op_key: plan.opKey }, message);
  }
}

async function resolveOrder(q: pg.Pool, event: EventRow): Promise<OrderRow | null> {
  const metaOrder = event.payload?.data?.object?.metadata?.order_id;
  if (metaOrder) {
    const byMeta = await one<OrderRow>(q, `select id, status, version, amount_minor, currency from orders where org_id = $1 and id::text = $2`, [event.org_id, metaOrder]);
    if (byMeta) return byMeta;
  }
  if (event.provider_payment_ref) {
    return one<OrderRow>(
      q,
      `select o.id, o.status, o.version, o.amount_minor, o.currency from orders o
       join payment_attempts pa on pa.order_id = o.id where pa.org_id = $1 and pa.provider_payment_ref = $2`,
      [event.org_id, event.provider_payment_ref],
    );
  }
  return null;
}

interface EffectPlan { opKey: string; orderStatus: string; attemptStatus: string | null; note?: string; }

function planEffect(event: EventRow, order: OrderRow): EffectPlan | null {
  const ref = event.provider_payment_ref ?? "unknown";
  switch (event.event_type) {
    case "payment_intent.succeeded": {
      // A second successful payment for an already-paid order is never silently absorbed.
      if (order.status === "paid") {
        return { opKey: `order:${order.id}:paid:${ref}`, orderStatus: "needs_review", attemptStatus: "succeeded", note: "second successful payment on a paid order" };
      }
      return { opKey: `order:${order.id}:paid:${ref}`, orderStatus: "paid", attemptStatus: "succeeded" };
    }
    case "payment_intent.payment_failed":
      if (order.status === "paid" || order.status === "needs_review") return { opKey: `order:${order.id}:failed:${ref}`, orderStatus: order.status, attemptStatus: "failed", note: "failure for a non-current attempt" };
      return { opKey: `order:${order.id}:failed:${ref}`, orderStatus: "failed", attemptStatus: "failed" };
    case "payment_intent.requires_action":
      if (order.status !== "pending") return null;
      return { opKey: `order:${order.id}:requires_action:${ref}`, orderStatus: "requires_action", attemptStatus: "requires_action" };
    default:
      return null;
  }
}
