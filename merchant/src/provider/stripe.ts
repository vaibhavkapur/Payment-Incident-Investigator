import Stripe from "stripe";
import type { ConfirmInput, CreatePaymentInput, ProviderAdapter, ProviderPayment } from "./types.js";

/**
 * Real Stripe sandbox adapter. Webhooks are delivered by Stripe (forward them with
 * `stripe listen --forward-to localhost:4100/webhooks/stripe`). Test payment methods
 * select the outcome: https://docs.stripe.com/testing
 */
export class StripeAdapter implements ProviderAdapter {
  readonly environment = "sandbox" as const;
  private stripe: Stripe;

  constructor(secretKey: string) {
    this.stripe = new Stripe(secretKey);
  }

  async createPayment(input: CreatePaymentInput): Promise<ProviderPayment> {
    const pi = await this.stripe.paymentIntents.create(
      {
        amount: input.amountMinor,
        currency: input.currency,
        metadata: { order_id: input.orderId, org_id: input.connection.org_id },
        automatic_payment_methods: { enabled: true, allow_redirects: "never" },
      },
      { idempotencyKey: input.idempotencyKey },
    );
    return { paymentRef: pi.id, status: pi.status };
  }

  async confirmPayment(input: ConfirmInput): Promise<ProviderPayment> {
    const method =
      input.outcome === "failed" ? "pm_card_chargeDeclined"
      : input.outcome === "requires_action" ? "pm_card_authenticationRequired"
      : "pm_card_visa";
    const pi = await this.stripe.paymentIntents.confirm(input.paymentRef, { payment_method: method });
    return { paymentRef: pi.id, status: pi.status };
  }
}

export function constructVerifiedEvent(rawBody: Buffer | string, signatureHeader: string, secret: string): Stripe.Event {
  return Stripe.webhooks.constructEvent(rawBody, signatureHeader, secret);
}

export function signTestPayload(payload: string, secret: string): string {
  return Stripe.webhooks.generateTestHeaderString({ payload, secret });
}
