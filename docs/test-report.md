# Recorded Test Report

[Documentation](index.md) · [Getting Started](getting-started.md) · [Repository README](../README.md)

## Historical Baseline

These measurements were recorded on 20 October 2026 before this documentation reorganization. They are not a new execution of the integration suite.

Measured with the rule-based investigator (no Gemini key in this environment), simulated provider, on this machine. Reports are written to `evals/reports/`.

| Metric | Dev set (11 scenarios) | Held-out set (12 combos × 3 repeats, with distractors) |
|---|---|---|
| Diagnosis accuracy | 100% | 100% |
| Recommended action correct | 100% | 100% |
| Evidence references valid | 100% | 100% |
| Runs with unsupported claims | 0 | 0 |
| Appropriate escalation | 100% | 100% |
| Incorrect recovery proposals | 0 | 0 |
| Tool calls (mean / max) | 7.5 / 8 | 7.5 / 8 |
| Latency (median / p95) | 30 ms / 41 ms | 22 ms / 30 ms |
| Model cost | none (rules) | none (rules) |

These numbers describe the deterministic path and are mostly a check of the tooling, guard rails and scoring. They are not a claim about model quality. Running `make eval-heldout` with `GOOGLE_API_KEY` set produces the ADK figures, including token counts and variability across repeats; the runner records model and prompt version per row.

Required application checks (all in `api/tests/test_required_checks.py` and `merchant/test/`):

- Unsigned or tampered webhooks are rejected and never stored.
- Cross-organization incident, log and provider access is denied at the API and in the tool layer.
- No recovery executes without a valid, current approval (changed order version, expired or revoked approval, expired proposal are each refused).
- Repeated event processing creates no duplicate business effect.
- Worker restart neither loses an approved job nor applies it twice.
- A recovery action never creates a new charge (payment attempts and provider payments are counted before and after).
- The incident remains unresolved when verification is missing or contradictory.
- Instructions inside logs and complaints do not create proposals or change status.

The supplied [development report](reports/20261020T014031Z-dev.md) and [held-out report](reports/20261020T014240Z-heldout.md) are preserved for provenance. See [Testing](testing.md) to rerun them.

## Publication Validation

Documentation links, common guide structure, tracked-file exclusions, and preservation of application source passed publication checks. Application code is unchanged by the documentation reorganization.

## Verification on 21 October 2026

- Merchant: `npm run typecheck` passed.
- Console: `npm run build` passed (TypeScript and Vite).
- Database-backed integration tests, demo/evaluation runs, Gemini, Stripe sandbox, and hosted Supabase were not rerun.

These checks reused the supplied local dependency installations. They do not establish a fresh dependency install or production readiness.
