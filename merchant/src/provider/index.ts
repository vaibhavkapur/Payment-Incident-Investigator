import { config } from "../config.js";
import { SimulatedAdapter } from "./simulated.js";
import { StripeAdapter } from "./stripe.js";
import type { ProviderAdapter } from "./types.js";

export function createProvider(): ProviderAdapter {
  if (config.PROVIDER_MODE === "stripe") return new StripeAdapter(config.STRIPE_SECRET_KEY);
  return new SimulatedAdapter(`${config.MERCHANT_PUBLIC_URL}/webhooks/stripe`, config.SIMULATED_WEBHOOK_SECRET);
}
