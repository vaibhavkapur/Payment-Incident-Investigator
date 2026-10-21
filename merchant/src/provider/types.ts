export type ConfirmOutcome = "succeeded" | "requires_action" | "failed";

export interface ProviderConnection {
  id: string;
  org_id: string;
  provider: string;
  environment: "sandbox" | "simulated";
  account_ref: string;
}

export interface CreatePaymentInput {
  connection: ProviderConnection;
  orderId: string;
  amountMinor: number;
  currency: string;
  idempotencyKey: string;
}

export interface ProviderPayment {
  paymentRef: string;
  status: string;
}

export interface ConfirmInput {
  connection: ProviderConnection;
  orderId: string;
  paymentRef: string;
  outcome: ConfirmOutcome;
}

export interface ProviderAdapter {
  readonly environment: "sandbox" | "simulated";
  createPayment(input: CreatePaymentInput): Promise<ProviderPayment>;
  /** Confirms the payment. The simulator also schedules webhook delivery, honoring faults. */
  confirmPayment(input: ConfirmInput): Promise<ProviderPayment>;
}
