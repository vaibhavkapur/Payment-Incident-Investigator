# Database

[Documentation](index.md) · [Getting Started](getting-started.md) · [Repository README](../README.md)

## Schema

[0001_init.sql](../supabase/migrations/0001_init.sql) defines organizations and memberships; provider connections, orders, attempts, events, processing history, and business operations; application logs and simulator faults; incidents, evidence, diagnoses, proposals, approvals, recovery jobs, verification results, audit events, and agent runs.

[0002_seed.sql](../supabase/migrations/0002_seed.sql) creates local demonstration organizations, operators, and provider connections. `make up` starts PostgreSQL and applies both files through `make migrate`. For Supabase, apply the SQL using `psql` against the intended project; `make migrate` targets the local Docker container.

## Approval and Recovery

A proposal records the guarded order state and expiry. An approval binds organization, incident, original event, action type, version, and expiry. The worker claims jobs with `FOR UPDATE SKIP LOCKED` and revalidates authority before requesting reprocessing.

Merchant-side business-operation keys and recovery-job identifiers deduplicate effects. Verification compares provider and order state, applied processing, business operations, and payment counts before marking an incident resolved. Missing or contradictory verification leaves the case open for review.

## Isolation and Persistence

Operational tables have organization-scoped RLS read policies. Backend API and tool paths also enforce explicit membership and organization filters. ADK session tables use the separate `adk` schema. The Docker volume `pii-pgdata` stores local PostgreSQL data and is not part of the repository.

See [Architecture](architecture.md) and [Testing](testing.md) for the workflow and required recovery checks.
