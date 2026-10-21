# API Reference

[Documentation](index.md) · [Getting Started](getting-started.md) · [Repository README](../README.md)

The incident API runs at `http://localhost:8100`. Interactive schemas are at `/docs` and the OpenAPI document is at `/openapi.json`. The sample merchant runs separately on `http://localhost:4100`.

Local development identifies seeded users with `X-User-Id`. Supabase mode uses `Authorization: Bearer <token>`. Every organization-scoped route requires an actual membership; approval routes require the approver or admin role. Use `/dev/users` for local fixture identities, and see [auth.py](../api/app/auth.py) for enforcement.

`GET /health` reports API health. The inventory below is taken from the route implementation. The merchant's `POST /internal/reprocess` endpoint is a worker-authorized internal command; it is not an investigation-agent tool. See [Demo Script](demo-script.md) for an HTTP walkthrough.

## Incidents

[Route implementation](../api/app/routers/incidents.py)

- `POST /orgs/{org_id}/incidents`
- `GET /orgs/{org_id}/incidents`
- `GET /orgs/{org_id}/incidents/{incident_id}`
- `POST /orgs/{org_id}/incidents/{incident_id}/investigate`
- `POST /orgs/{org_id}/incidents/{incident_id}/assign`
- `POST /orgs/{org_id}/incidents/{incident_id}/escalate`
- `POST /orgs/{org_id}/incidents/{incident_id}/close`
- `GET /orgs/{org_id}/incidents/{incident_id}/report`
- `GET /orgs/{org_id}/incidents/{incident_id}/runs/{run_id}`

## Me

[Route implementation](../api/app/routers/me.py)

- `GET /me`
- `GET /dev/users`

## Proposals

[Route implementation](../api/app/routers/proposals.py)

- `POST /orgs/{org_id}/proposals/{proposal_id}/approve`
- `POST /orgs/{org_id}/proposals/{proposal_id}/reject`
