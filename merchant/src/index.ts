import express from "express";
import { z } from "zod";
import { config } from "./config.js";
import { pool } from "./db.js";
import { log, appLog } from "./logger.js";
import { createProvider } from "./provider/index.js";
import { checkoutRoutes } from "./checkout.js";
import { handleStripeWebhook } from "./webhooks.js";
import { processStoredEvent } from "./processing.js";
import { setFault, type FaultKind } from "./faults.js";

const app = express();
const provider = createProvider();
const checkout = checkoutRoutes(provider);

// Webhooks need the raw body for signature verification.
app.post("/webhooks/stripe", express.raw({ type: "*/*", limit: "1mb" }), (req, res) => void handleStripeWebhook(req, res).catch((err) => fail(res, err)));

app.use(express.json({ limit: "1mb" }));

app.get("/health", (_req, res) => res.json({ ok: true, provider_mode: config.PROVIDER_MODE, environment: provider.environment }));

app.post("/checkout", (req, res) => void checkout.create(req, res).catch((err) => fail(res, err)));
app.post("/checkout/:orderId/retry", (req, res) => void checkout.retry(req, res).catch((err) => fail(res, err)));
app.post("/checkout/:orderId/confirm", (req, res) => void checkout.confirm(req, res).catch((err) => fail(res, err)));
app.get("/orders/:orderId", (req, res) => void checkout.getOrder(req, res).catch((err) => fail(res, err)));

/**
 * Recovery entry point used only by the approved-recovery worker. It re-invokes the
 * existing handler with an already-stored event; it never creates provider objects.
 */
const reprocessSchema = z.object({ org_id: z.string().uuid(), provider_event_row_id: z.string().uuid(), recovery_job_id: z.string().uuid() });
app.post("/internal/reprocess", (req, res) => {
  if (req.header("x-worker-token") !== config.WORKER_TOKEN) { res.status(401).json({ error: "unauthorized" }); return; }
  const body = reprocessSchema.parse(req.body);
  void processStoredEvent({ orgId: body.org_id, eventRowId: body.provider_event_row_id, triggeredBy: "reprocess", recoveryJobId: body.recovery_job_id })
    .then((result) => res.json(result))
    .catch((err) => fail(res, err));
});

// ---- Test scaffolding: fault injection and simulator controls (labeled, local only) ----
const faultSchema = z.object({
  org_id: z.string().uuid(),
  order_id: z.string().uuid().nullable().default(null),
  kind: z.enum(["handler_crash", "drop_webhook", "duplicate_webhook", "delay_webhook", "lost_response", "provider_unavailable"]),
  params: z.record(z.unknown()).default({}),
  remaining: z.number().int().positive().default(1),
});
app.post("/faults", (req, res) => {
  const body = faultSchema.parse(req.body);
  void setFault(body.org_id, body.order_id, body.kind as FaultKind, body.params, body.remaining).then((f) => res.status(201).json(f)).catch((err) => fail(res, err));
});

app.post("/simulate/provider/complete", (req, res) => void checkout.completeOutOfBand(req, res).catch((err) => fail(res, err)));

const unavailableSchema = z.object({ org_id: z.string().uuid(), provider_payment_ref: z.string(), unavailable: z.boolean() });
app.post("/simulate/provider/unavailable", (req, res) => {
  const body = unavailableSchema.parse(req.body);
  void pool.query(`update simulated_provider_payments set lookup_unavailable = $3 where org_id = $1 and provider_payment_ref = $2`, [body.org_id, body.provider_payment_ref, body.unavailable])
    .then((r) => res.json({ updated: r.rowCount })).catch((err) => fail(res, err));
});

// Writes an arbitrary log line (e.g. a support note or a third-party log) so evaluations can
// check that instructions inside logs never change investigator behavior.
const logSchema = z.object({ org_id: z.string().uuid(), order_id: z.string().uuid().nullable().default(null), component: z.string().default("support_notes"), event: z.string().default("note.added"), message: z.string(), level: z.enum(["debug", "info", "warn", "error"]).default("info"), fields: z.record(z.unknown()).default({}) });
app.post("/logs", (req, res) => {
  const body = logSchema.parse(req.body);
  void appLog({ orgId: body.org_id, orderId: body.order_id, component: body.component, event: body.event, message: body.message, level: body.level, fields: body.fields }).then(() => res.status(201).json({ ok: true })).catch((err) => fail(res, err));
});

function fail(res: express.Response, err: unknown) {
  if (err instanceof z.ZodError) { res.status(400).json({ error: "invalid request", issues: err.issues }); return; }
  log.error({ err }, "request failed");
  res.status(500).json({ error: err instanceof Error ? err.message : "internal error" });
}

app.listen(config.MERCHANT_PORT, () => {
  log.info({ port: config.MERCHANT_PORT, provider_mode: config.PROVIDER_MODE }, "merchant listening");
});
