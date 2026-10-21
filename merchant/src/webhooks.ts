import type { Request, Response } from "express";
import { pool, one } from "./db.js";
import { appLog } from "./logger.js";
import { config, providerEnvironment, webhookSecret } from "./config.js";
import { constructVerifiedEvent } from "./provider/stripe.js";
import { redactEvent } from "./redact.js";
import { processStoredEvent } from "./processing.js";
import { consumeFault } from "./faults.js";

interface StoredEvent { id: string; org_id: string; delivery_count: number; inserted: boolean; }

/**
 * Verified webhook collector. An event becomes trusted evidence only after its
 * signature verifies against the connection's webhook secret. Duplicate deliveries
 * update delivery_count on the existing row instead of creating a second event.
 */
export async function handleStripeWebhook(req: Request, res: Response): Promise<void> {
  const signature = req.header("stripe-signature") ?? "";
  const raw = req.body as Buffer;
  let event;
  try {
    event = constructVerifiedEvent(raw, signature, webhookSecret());
  } catch (err) {
    // Unverified payloads are never stored as provider events. We log the rejection without the body.
    await appLog({ orgId: orgFromUnverified(raw) ?? "00000000-0000-0000-0000-000000000000", level: "warn", component: "webhook_collector", event: "webhook.signature_invalid", message: `Rejected webhook: ${err instanceof Error ? err.message : String(err)}` }).catch(() => undefined);
    res.status(400).json({ error: "signature verification failed" });
    return;
  }

  const object = event.data.object as { id?: string; object?: string; metadata?: Record<string, string> };
  const orgId = object.metadata?.org_id;
  if (!orgId) {
    res.status(202).json({ ignored: "event has no org_id metadata" });
    return;
  }
  const connection = await one<{ id: string }>(pool, `select id from provider_connections where org_id = $1 and provider = 'stripe' limit 1`, [orgId]);
  if (!connection) {
    res.status(202).json({ ignored: "no provider connection for organization" });
    return;
  }
  const paymentRef = object.object === "payment_intent" ? object.id ?? null : null;
  const orderId = object.metadata?.order_id ?? null;

  // Sandbox-mode fault: the endpoint fails before persisting (Stripe will retry later).
  if (config.PROVIDER_MODE === "stripe" && orderId) {
    const drop = await consumeFault(orgId, orderId, "drop_webhook");
    if (drop) {
      await appLog({ orgId, level: "error", component: "webhook_collector", event: "webhook.persist_failed", message: "Failed to persist webhook: upstream timeout", orderId, providerPaymentRef: paymentRef, providerEventId: event.id });
      res.status(500).json({ error: "temporary failure" });
      return;
    }
  }

  const stored = await one<StoredEvent>(
    pool,
    `insert into provider_events (org_id, provider_connection_id, provider_event_id, event_type, provider_payment_ref, environment, signature_verified, payload, event_time)
     values ($1, $2, $3, $4, $5, $6, true, $7, to_timestamp($8))
     on conflict (org_id, provider_event_id) do update set delivery_count = provider_events.delivery_count + 1, last_received_at = now()
     returning id, org_id, delivery_count, (xmax = 0) as inserted`,
    [orgId, connection.id, event.id, event.type, paymentRef, providerEnvironment(), JSON.stringify(redactEvent(event as unknown as Record<string, unknown>)), event.created],
  );
  await appLog({
    orgId, component: "webhook_collector", event: stored!.inserted ? "webhook.received" : "webhook.duplicate_delivery",
    message: stored!.inserted ? `Stored verified ${event.type}` : `Duplicate delivery #${stored!.delivery_count} of ${event.id}`,
    orderId, providerPaymentRef: paymentRef, providerEventId: event.id, requestId: req.header("x-request-id") ?? null,
    fields: { provider_event_row_id: stored!.id, delivery_count: stored!.delivery_count, environment: providerEnvironment() },
  });

  // Respond quickly; processing is recorded separately and can be reprocessed later.
  res.status(200).json({ received: true, provider_event_row_id: stored!.id, delivery_count: stored!.delivery_count });
  try {
    await processStoredEvent({ orgId, eventRowId: stored!.id, triggeredBy: "webhook", requestId: req.header("x-request-id") ?? null });
  } catch (err) {
    await appLog({ orgId, level: "error", component: "webhook_collector", event: "processing.unhandled_error", message: err instanceof Error ? err.message : String(err), orderId, providerEventId: event.id });
  }
}

function orgFromUnverified(raw: Buffer): string | null {
  try {
    const parsed = JSON.parse(raw.toString("utf8"));
    const org = parsed?.data?.object?.metadata?.org_id;
    return typeof org === "string" && /^[0-9a-f-]{36}$/.test(org) ? org : null;
  } catch {
    return null;
  }
}
