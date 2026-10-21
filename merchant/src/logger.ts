import pino from "pino";
import { pool, type Queryable } from "./db.js";
import { config } from "./config.js";

export const log = pino({ level: config.LOG_LEVEL, base: { service: "merchant" } });

export interface AppLogEntry {
  orgId: string;
  level?: "debug" | "info" | "warn" | "error";
  component: string;
  event: string;
  message: string;
  orderId?: string | null;
  providerPaymentRef?: string | null;
  providerEventId?: string | null;
  requestId?: string | null;
  fields?: Record<string, unknown>;
  ts?: Date;
}

/**
 * Structured application log. Every entry goes to stdout (pino) and to the
 * application_logs table so the investigator can query bounded fields.
 * Callers must redact customer and payment details before logging.
 */
export async function appLog(entry: AppLogEntry, q: Queryable = pool): Promise<void> {
  const level = entry.level ?? "info";
  log[level]({ ...entry.fields, org_id: entry.orgId, order_id: entry.orderId, event: entry.event }, entry.message);
  await q.query(
    `insert into application_logs (org_id, ts, level, component, event, order_id, provider_payment_ref, provider_event_id, request_id, message, fields)
     values ($1, coalesce($2, now()), $3, $4, $5, $6, $7, $8, $9, $10, $11)`,
    [
      entry.orgId,
      entry.ts ?? null,
      level,
      entry.component,
      entry.event,
      entry.orderId ?? null,
      entry.providerPaymentRef ?? null,
      entry.providerEventId ?? null,
      entry.requestId ?? null,
      entry.message,
      JSON.stringify(entry.fields ?? {}),
    ],
  );
}
