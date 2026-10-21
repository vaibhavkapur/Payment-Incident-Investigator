import { randomBytes } from "node:crypto";
import { pool } from "../db.js";
import { appLog } from "../logger.js";
import { consumeFault } from "../faults.js";
import { signTestPayload } from "./stripe.js";
import type { ConfirmInput, CreatePaymentInput, ProviderAdapter, ProviderPayment } from "./types.js";

function rid(prefix: string): string {
  return `${prefix}_sim_${randomBytes(9).toString("hex")}`;
}

/**
 * Local Stripe-shaped simulator. It keeps provider state in simulated_provider_payments
 * and delivers webhooks to the merchant's own endpoint, signed with the real Stripe
 * signature scheme, so the verification path exercised is the production one.
 * Everything it produces is labeled environment=simulated.
 */
export class SimulatedAdapter implements ProviderAdapter {
  readonly environment = "simulated" as const;

  constructor(private readonly webhookUrl: string, private readonly webhookSecret: string) {}

  async createPayment(input: CreatePaymentInput): Promise<ProviderPayment> {
    // Idempotent on (org, idempotency key), like the provider.
    const existing = await pool.query(
      `select provider_payment_ref, status from simulated_provider_payments where org_id = $1 and idempotency_key = $2`,
      [input.connection.org_id, input.idempotencyKey],
    );
    if (existing.rows[0]) return { paymentRef: existing.rows[0].provider_payment_ref, status: existing.rows[0].status };
    const paymentRef = rid("pi");
    await pool.query(
      `insert into simulated_provider_payments (org_id, provider_payment_ref, status, amount_minor, currency, idempotency_key, metadata)
       values ($1, $2, 'requires_confirmation', $3, $4, $5, $6)`,
      [input.connection.org_id, paymentRef, input.amountMinor, input.currency, input.idempotencyKey, JSON.stringify({ order_id: input.orderId, org_id: input.connection.org_id })],
    );
    return { paymentRef, status: "requires_confirmation" };
  }

  async confirmPayment(input: ConfirmInput): Promise<ProviderPayment> {
    const orgId = input.connection.org_id;
    const status = input.outcome === "succeeded" ? "succeeded" : input.outcome === "requires_action" ? "requires_action" : "requires_payment_method";
    const row = await pool.query(
      `update simulated_provider_payments set status = $3, updated_at = now() where org_id = $1 and provider_payment_ref = $2 returning *`,
      [orgId, input.paymentRef, status],
    );
    const payment = row.rows[0];
    if (!payment) throw new Error(`simulated provider: unknown payment ${input.paymentRef}`);

    const eventType =
      input.outcome === "succeeded" ? "payment_intent.succeeded"
      : input.outcome === "requires_action" ? "payment_intent.requires_action"
      : "payment_intent.payment_failed";

    const event = {
      id: rid("evt"),
      object: "event",
      api_version: "2025-01-27.acacia",
      created: Math.floor(Date.now() / 1000),
      livemode: false,
      type: eventType,
      request: { id: null, idempotency_key: payment.idempotency_key },
      data: {
        object: {
          id: payment.provider_payment_ref,
          object: "payment_intent",
          amount: Number(payment.amount_minor),
          amount_received: input.outcome === "succeeded" ? Number(payment.amount_minor) : 0,
          currency: payment.currency,
          status,
          created: Math.floor(new Date(payment.created_at).getTime() / 1000),
          metadata: payment.metadata,
          // Fields a real event carries that the merchant must redact before storing:
          receipt_email: "customer@example.test",
          payment_method_details: { card: { last4: "4242", brand: "visa" } },
          client_secret: `${payment.provider_payment_ref}_secret_${randomBytes(6).toString("hex")}`,
        },
      },
    };
    void this.deliver(orgId, input.orderId, event);
    return { paymentRef: payment.provider_payment_ref, status };
  }

  /** Delivers the event as a signed webhook, honoring drop/duplicate/delay faults. */
  private async deliver(orgId: string, orderId: string, event: Record<string, unknown>): Promise<void> {
    const drop = await consumeFault(orgId, orderId, "drop_webhook");
    if (drop) {
      // Nothing reaches the merchant. The only trace is the provider's own record.
      return;
    }
    const delay = await consumeFault(orgId, orderId, "delay_webhook");
    const delayMs = delay ? Number(delay.params.delay_ms ?? 3000) : 0;
    const duplicate = await consumeFault(orgId, orderId, "duplicate_webhook");
    const copies = duplicate ? Number(duplicate.params.copies ?? 2) : 1;

    const payload = JSON.stringify(event);
    const send = async () => {
      const header = signTestPayload(payload, this.webhookSecret);
      try {
        await fetch(this.webhookUrl, {
          method: "POST",
          headers: { "content-type": "application/json", "stripe-signature": header },
          body: payload,
        });
      } catch (err) {
        await appLog({ orgId, level: "error", component: "simulated_provider", event: "webhook.delivery_failed", message: String(err), orderId });
      }
    };
    const run = async () => {
      for (let i = 0; i < copies; i++) await send();
    };
    if (delayMs > 0) setTimeout(() => void run(), delayMs);
    else await run();
  }
}
