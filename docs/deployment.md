# Deployment

[Documentation](index.md) · [Getting Started](getting-started.md) · [Repository README](../README.md)

## Current Status

The supplied system runs locally against PostgreSQL and a Stripe-shaped simulator. A real Stripe sandbox path is implemented but has not been exercised in the recorded baseline. A hosted demo and operational monitoring are not included; no live payment support is claimed.

## Hosted Prerequisites

Apply [Configuration](configuration.md) for Supabase/PostgreSQL and authenticated access. Replace `AUTH_MODE=dev` with JWT verification and provision organization memberships. Set a dedicated `WORKER_TOKEN`, keep provider credentials server-side, restrict the internal merchant endpoint, and configure CORS and TLS for actual service origins.

Build the console with `npm run build` in `web/` and set its public API configuration for the hosted service. The Makefile's development targets use reload/watch and are not production process supervision.

The API embeds a recovery worker by default. A separately supervised worker can use `make worker` with `WORKER_EMBEDDED=false` in the API environment. Validate job claiming, stale-lock recovery, backups, migrations, and restarts on the deployed database.

Before claiming a provider-backed deployment, run the Stripe sandbox and model-backed paths, verify hosted RLS and authentication, and add monitoring. Refunds, new charges as remediation, provider settings changes, and live accounts remain outside the current product scope.
