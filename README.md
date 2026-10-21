# Payment Incident Investigator

An assistant for payment operations teams that reconstructs a payment incident, explains the evidence, and guides an operator through an approval-bound recovery action.

> **[Read the full documentation](docs/index.md)**

Built with FastAPI, Google ADK, React, TypeScript, and PostgreSQL, plus an Express sample merchant. The local MVP uses a Stripe-shaped simulator and a deterministic investigator when no Gemini key is configured. The real Stripe sandbox adapter and model-backed investigation are implemented but were not exercised in the recorded baseline. Recovery reprocesses an existing event; it does not create a new charge.

## Getting Started

```bash
# Requires Docker, Python 3.12+, Node.js 20.19+ (or 22.12+), npm, and Make
git clone https://github.com/vaibhavkapur/Payment-Incident-Investigator.git
cd Payment-Incident-Investigator
cp .env.example .env
make setup
make up
```

From the repository root, run each service in its own terminal:

```bash
make merchant   # http://localhost:4100
make api        # http://localhost:8100
make web        # http://localhost:5173
```

See [Getting Started](docs/getting-started.md) for seeded operators and local verification, and [Configuration](docs/configuration.md) for Gemini, Stripe, and Supabase.

## Quick Example

With the services running, run `make demo` from another terminal. It creates a payment whose stored webhook failed to update the order, investigates the evidence, obtains approval, reprocesses the stored event, and verifies the outcome without another charge. Replaying the recovery is a no-op. A second incident with an incomplete payment refuses recovery.

See the [Demo Script](docs/demo-script.md), [Testing](docs/testing.md), and [Recorded Test Report](docs/test-report.md). Refunds, new-charge remediation, live payment accounts, a hosted demo, and operational monitoring remain outside this MVP.
