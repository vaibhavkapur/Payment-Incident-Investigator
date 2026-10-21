# Payment Incident Investigator

An assistant for payment operations teams that reconstructs a payment incident, explains the evidence, and guides an operator through an approval-bound recovery action.

[Get Started](getting-started.md) · [API Reference](api-reference.md) · [Repository README](../README.md)

## Key Features

- Tenant-scoped incident timelines with provider, application, webhook, and ingestion evidence.
- Google ADK investigation or deterministic fallback using the same scoped tools and guard checks.
- Version-bound approval, durable recovery jobs, idempotent event processing, and post-action verification.
- Operations console, audit reports, scenario evaluation, and a sample merchant with labeled faults.

## Tech Stack and Scope

Python 3.12+, FastAPI, Google ADK, PostgreSQL, Node.js / Express / TypeScript, and React / Vite. The local baseline uses a simulated provider and no model credentials. Stripe sandbox and hosted Supabase paths require separate configuration and validation. No new charges or refunds are performed as recovery.

## Documentation

- [Getting Started](getting-started.md)
- [Architecture](architecture.md)
- [API Reference](api-reference.md)
- [Configuration](configuration.md)
- [Database](database.md)
- [Testing](testing.md)
- [Deployment](deployment.md)
- [Demo Script](demo-script.md)
- [Recorded Test Report](test-report.md)


## Project Structure

```
merchant/   Node sample checkout, provider adapters, webhook collector, handler, faults, tests
api/        FastAPI app, auth, services (tools, proposals, approvals, recovery, verification), ADK agent, worker, tests
web/        React console
supabase/   migrations (schema + RLS) and seed
evals/      scenario corpus and evaluation runner; reports/ is generated
scripts/    demo.sh
```

## Related Projects

These are independent companion repositories, not runtime dependencies or claims of implemented integrations.

- [Accounts Payable Exception Agent](https://github.com/vaibhavkapur/Accounts-Payable-Exception-Agent)
- [Merchant Integration Copilot](https://github.com/vaibhavkapur/Merchant-Integration-Copilot)
