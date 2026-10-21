# Demo Script

[Documentation](index.md) · [Getting Started](getting-started.md) · [Repository README](../README.md)

## Start the Services

Follow [Getting Started](getting-started.md), leaving PostgreSQL, merchant, and API running in local simulator mode. The console is useful for inspection but the script uses HTTP APIs.

```bash
make demo
```

The executable script is [scripts/demo.sh](../scripts/demo.sh).

## Expected Story


A customer reports a charge, but the merchant's order remains unpaid. The investigator retrieves the provider's payment state, finds the corresponding stored webhook, and identifies an application processing failure. It proposes reprocessing that existing event. An approver reviews the guard checks and approves; a worker revalidates the approval, reprocesses the stored event through the merchant's normal handler, and eight verification checks confirm the order is paid with no new charge. Replaying the action afterwards is a no-op.

`make demo` runs exactly this, followed by a look-alike incident where the payment is incomplete and recovery is refused.

Open the console to inspect the incident timeline, evidence, diagnosis, proposal guard checks, approval, verification, audit, and report. The seeded analyst can investigate; approver/admin roles approve. This demo uses simulated provider outcomes and does not contact a live payment account.
