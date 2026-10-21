/**
 * Redaction for provider payloads before they are stored or shown to a model.
 * Keeps identifiers, amounts, statuses and timing; drops card, customer and
 * contact details that an investigator does not need.
 */
const DROP_KEYS = new Set([
  "charges", "payment_method_details", "billing_details", "shipping", "receipt_email", "receipt_url",
  "customer", "customer_details", "payment_method", "last_payment_error", "client_secret", "source",
  "latest_charge", "next_action", "description",
]);

export function redactDeep<T>(value: T, depth = 0): T {
  if (depth > 8 || value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map((v) => redactDeep(v, depth + 1)) as unknown as T;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    if (DROP_KEYS.has(k)) continue;
    out[k] = redactDeep(v, depth + 1);
  }
  return out as T;
}

export function redactEvent(event: Record<string, unknown>): Record<string, unknown> {
  const data = (event.data as Record<string, unknown> | undefined) ?? {};
  const object = (data.object as Record<string, unknown> | undefined) ?? {};
  return {
    id: event.id,
    object: "event",
    type: event.type,
    created: event.created,
    livemode: event.livemode ?? false,
    api_version: event.api_version,
    request: event.request ? redactDeep(event.request) : null,
    data: { object: redactDeep(object) },
  };
}
