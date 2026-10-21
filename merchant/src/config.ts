import { z } from "zod";

const schema = z.object({
  DATABASE_URL: z.string().default("postgresql://pii:pii@localhost:54329/pii"),
  MERCHANT_PORT: z.coerce.number().default(4100),
  MERCHANT_PUBLIC_URL: z.string().default("http://localhost:4100"),
  WORKER_TOKEN: z.string().min(8).default("change-me-worker-token"),
  PROVIDER_MODE: z.enum(["simulated", "stripe"]).default("simulated"),
  STRIPE_SECRET_KEY: z.string().optional().default(""),
  STRIPE_WEBHOOK_SECRET: z.string().optional().default(""),
  SIMULATED_WEBHOOK_SECRET: z.string().default("whsec_local_simulated_secret"),
  LOG_LEVEL: z.string().default("info"),
});

export type Config = z.infer<typeof schema>;

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const cfg = schema.parse(env);
  if (cfg.PROVIDER_MODE === "stripe") {
    if (!cfg.STRIPE_SECRET_KEY.startsWith("sk_test_")) {
      throw new Error("PROVIDER_MODE=stripe requires a Stripe sandbox/test secret key (sk_test_...). Live keys are refused.");
    }
    if (!cfg.STRIPE_WEBHOOK_SECRET) {
      throw new Error("PROVIDER_MODE=stripe requires STRIPE_WEBHOOK_SECRET (from `stripe listen`).");
    }
  }
  return cfg;
}

export const config = loadConfig();

/** The webhook signing secret for the active provider mode. */
export function webhookSecret(): string {
  return config.PROVIDER_MODE === "stripe" ? config.STRIPE_WEBHOOK_SECRET : config.SIMULATED_WEBHOOK_SECRET;
}

export function providerEnvironment(): "sandbox" | "simulated" {
  return config.PROVIDER_MODE === "stripe" ? "sandbox" : "simulated";
}
