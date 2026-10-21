import { randomUUID } from "node:crypto";
import type { Request, Response } from "express";
import { z } from "zod";
import { pool, one } from "./db.js";
import { appLog } from "./logger.js";
import { consumeFault } from "./faults.js";
import type { ProviderAdapter, ProviderConnection } from "./provider/types.js";

const uuid = z.string().uuid();

const createSchema = z.object({
  org_id: uuid,
  amount_minor: z.number().int().positive(),
  currency: z.string().length(3).default("usd"),
  customer_ref: z.string().min(1).max(64).default(() => `cust_${randomUUID().slice(0, 8)}`),
  external_ref: z.string().min(1).max(64).optional(),
});

const confirmSchema = z.object({
  outcome: z.enum(["succeeded", "requires_action", "failed"]).default("succeeded"),
});

export function checkoutRoutes(provider: ProviderAdapter) {
  async function connectionFor(orgId: string): Promise<ProviderConnection | null> {
    return one<ProviderConnection>(pool, `select * from provider_connections where org_id = $1 and provider = 'stripe' limit 1`, [orgId]);
  }

  async function createAttempt(connection: ProviderConnection, order: { id: string; amount_minor: string | number; currency: string }, requestId: string) {
    const idempotencyKey = `order-${order.id}-attempt-${randomUUID().slice(0, 8)}`;
    const payment = await provider.createPayment({ connection, orderId: order.id, amountMinor: Number(order.amount_minor), currency: order.currency, idempotencyKey });
    const attempt = await one<{ id: string }>(
      pool,
      `insert into payment_attempts (org_id, order_id, provider_connection_id, provider_payment_ref, idempotency_key, status, amount_minor, currency)
       values ($1, $2, $3, $4, $5, $6, $7, $8) returning id`,
      [connection.org_id, order.id, connection.id, payment.paymentRef, idempotencyKey, payment.status, order.amount_minor, order.currency],
    );
    await appLog({ orgId: connection.org_id, component: "checkout", event: "payment.created", message: `Created provider payment ${payment.paymentRef}`, orderId: order.id, providerPaymentRef: payment.paymentRef, requestId, fields: { idempotency_key: idempotencyKey, environment: provider.environment } });
    return { id: attempt!.id, provider_payment_ref: payment.paymentRef, status: payment.status, idempotency_key: idempotencyKey };
  }

  return {
    async create(req: Request, res: Response) {
      const body = createSchema.parse(req.body);
      const requestId = randomUUID();
      const connection = await connectionFor(body.org_id);
      if (!connection) { res.status(404).json({ error: "organization has no provider connection" }); return; }
      const order = await one<{ id: string; amount_minor: string; currency: string }>(
        pool,
        `insert into orders (org_id, external_ref, customer_ref, amount_minor, currency, status) values ($1, $2, $3, $4, $5, 'pending') returning id, amount_minor, currency`,
        [body.org_id, body.external_ref ?? `ORD-${randomUUID().slice(0, 8).toUpperCase()}`, body.customer_ref, body.amount_minor, body.currency],
      );
      await appLog({ orgId: body.org_id, component: "checkout", event: "order.created", message: `Order created (${body.amount_minor} ${body.currency})`, orderId: order!.id, requestId, fields: { amount_minor: body.amount_minor, currency: body.currency } });
      const attempt = await createAttempt(connection, order!, requestId);
      res.status(201).json({ order_id: order!.id, request_id: requestId, attempt });
    },

    async retry(req: Request, res: Response) {
      const orgId = uuid.parse(req.body?.org_id);
      const order = await one<{ id: string; amount_minor: string; currency: string; status: string }>(pool, `select id, amount_minor, currency, status from orders where org_id = $1 and id = $2`, [orgId, req.params.orderId]);
      if (!order) { res.status(404).json({ error: "order not found" }); return; }
      const connection = (await connectionFor(orgId))!;
      const requestId = randomUUID();
      await appLog({ orgId, level: "warn", component: "checkout", event: "payment.retry_requested", message: `Customer retried payment while order status=${order.status}`, orderId: order.id, requestId });
      const attempt = await createAttempt(connection, order, requestId);
      res.status(201).json({ order_id: order.id, request_id: requestId, attempt });
    },

    async confirm(req: Request, res: Response) {
      const orgId = uuid.parse(req.body?.org_id);
      const { outcome } = confirmSchema.parse(req.body);
      const requestId = randomUUID();
      const attempt = await one<{ id: string; order_id: string; provider_payment_ref: string }>(
        pool,
        `select id, order_id, provider_payment_ref from payment_attempts where org_id = $1 and order_id = $2 and status in ('requires_confirmation','requires_payment_method','requires_action') order by created_at desc limit 1`,
        [orgId, req.params.orderId],
      );
      if (!attempt) { res.status(409).json({ error: "no confirmable payment attempt for order" }); return; }
      const connection = (await connectionFor(orgId))!;
      await appLog({ orgId, component: "checkout", event: "payment.confirm_requested", message: `Confirming ${attempt.provider_payment_ref}`, orderId: attempt.order_id, providerPaymentRef: attempt.provider_payment_ref, requestId });

      const result = await provider.confirmPayment({ connection, orderId: attempt.order_id, paymentRef: attempt.provider_payment_ref, outcome });
      await pool.query(`update payment_attempts set status = $2, updated_at = now() where id = $1`, [attempt.id, result.status]);

      // Local fault: the provider accepted the payment but the customer never got the response.
      const lost = await consumeFault(orgId, attempt.order_id, "lost_response");
      if (lost) {
        await appLog({ orgId, level: "error", component: "checkout", event: "checkout.response_failed", message: "Upstream request timed out while rendering confirmation (504)", orderId: attempt.order_id, providerPaymentRef: attempt.provider_payment_ref, requestId, fields: { provider_status: result.status } });
        await pool.query(`update orders set checkout_response = 'error_page', updated_at = now() where id = $1`, [attempt.order_id]);
        res.status(504).json({ error: "gateway timeout", request_id: requestId });
        return;
      }

      const page = result.status === "succeeded" ? "success" : result.status === "requires_action" ? "requires_action" : "failure";
      await pool.query(`update orders set checkout_response = $2, updated_at = now() where id = $1`, [attempt.order_id, page]);
      await appLog({ orgId, component: "checkout", event: "checkout.response_rendered", message: `Customer shown ${page} page`, orderId: attempt.order_id, providerPaymentRef: attempt.provider_payment_ref, requestId, fields: { provider_status: result.status } });
      res.json({ order_id: attempt.order_id, provider_payment_ref: attempt.provider_payment_ref, provider_status: result.status, customer_page: page, request_id: requestId });
    },

    async completeOutOfBand(req: Request, res: Response) {
      if (provider.environment !== "simulated") { res.status(400).json({ error: "only available with the simulated provider" }); return; }
      const orgId = uuid.parse(req.body?.org_id);
      const attempt = await one<{ id: string; order_id: string; provider_payment_ref: string }>(
        pool,
        `select id, order_id, provider_payment_ref from payment_attempts where org_id = $1 and provider_payment_ref = $2`,
        [orgId, String(req.body?.provider_payment_ref ?? "")],
      );
      if (!attempt) { res.status(404).json({ error: "payment attempt not found" }); return; }
      const connection = (await connectionFor(orgId))!;
      const result = await provider.confirmPayment({ connection, orderId: attempt.order_id, paymentRef: attempt.provider_payment_ref, outcome: "succeeded" });
      // Merchant-observed attempt status is refreshed only by the later webhook, as in production.
      res.json({ provider_payment_ref: attempt.provider_payment_ref, provider_status: result.status, note: "completed out of band; webhook delivery scheduled" });
    },

    async getOrder(req: Request, res: Response) {
      const orgId = uuid.parse(req.query.org_id ?? req.header("x-org-id"));
      const order = await one(pool, `select * from orders where org_id = $1 and id = $2`, [orgId, req.params.orderId]);
      if (!order) { res.status(404).json({ error: "order not found" }); return; }
      const attempts = await pool.query(`select * from payment_attempts where org_id = $1 and order_id = $2 order by created_at`, [orgId, req.params.orderId]);
      res.json({ order, attempts: attempts.rows });
    },
  };
}
