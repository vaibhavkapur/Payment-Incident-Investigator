# Configuration

[Documentation](index.md) · [Getting Started](getting-started.md) · [Repository README](../README.md)

Copy [.env.example](../.env.example) to `.env` at the repository root. The Makefile exports its values to each service. Keep provider and worker secrets on the server; only public API/auth configuration belongs in `VITE_` variables.

## Local Defaults

- `DATABASE_URL`: local PostgreSQL at `localhost:54329`.
- `PROVIDER_MODE=simulated`: local provider fixture; `AUTH_MODE=dev`: seeded-user headers.
- `AGENT_MODE=auto`: ADK when credentials are available, otherwise deterministic.
- `MERCHANT_PORT=4100`, `API_PORT=8100`; the console listens on port `5173`.
- `WORKER_TOKEN`: shared credential for the internal reprocessing endpoint; replace the development placeholder outside a local fixture.

## Using Google ADK with Gemini

Set `GOOGLE_API_KEY` in `.env` (or `GOOGLE_GENAI_USE_VERTEXAI=TRUE` with Vertex credentials). `AGENT_MODE=auto` then selects the ADK investigator; `ADK_MODEL` defaults to `gemini-2.5-flash`. Without a key the rule-based investigator runs and the console says so. The ADK agent has not been run against a live model in this repository yet, so its evaluation numbers are not reported below.

## Using a real Stripe sandbox

In `.env` set `PROVIDER_MODE=stripe`, `STRIPE_SECRET_KEY=sk_test_...` (live keys are refused at startup) and `STRIPE_WEBHOOK_SECRET` from `stripe listen --forward-to localhost:4100/webhooks/stripe`. Update the organization's `provider_connections.environment` to `sandbox`. Outcomes are chosen with Stripe test payment methods (`pm_card_visa`, `pm_card_authenticationRequired`, `pm_card_chargeDeclined`). The simulator-only faults (`drop_webhook` timing, `provider_unavailable`, out-of-band completion) have merchant-side equivalents or are skipped by the evaluation runner. This path is implemented but has not been executed against a live sandbox account.

## Using Supabase

Point `DATABASE_URL` at the project's direct connection string and apply `supabase/migrations/*.sql` with `psql`. Set `AUTH_MODE=supabase` and `SUPABASE_JWT_SECRET`; the API then verifies Supabase access tokens and still requires a `memberships` row for the requested organization. The console uses Supabase Realtime when `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` are set, otherwise it polls. ADK session tables live in the `adk` schema so they are never exposed through the public API schema.

Complete API defaults, including approval expiry, worker polling, tool budgets, and CORS, are in [api/app/config.py](../api/app/config.py). The API starts an embedded worker by default. Set `WORKER_EMBEDDED=false` before using `make worker` as a separate process.
