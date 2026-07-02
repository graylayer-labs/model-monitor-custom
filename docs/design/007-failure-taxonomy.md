# 007 — Failure taxonomy

**Status:** Accepted.
**Related:** [`006-observability-contract.md`](006-observability-contract.md), [`002-container-base.md`](002-container-base.md).

## Context

`ml-iac` proved a two-state `Completed` / `Failed` outcome hides what actually happened — was the run skipped for lack of data, did the container crash, did the analyser detect a real violation, or did an upstream artefact go missing? Alerting on the wrong axis fires false positives; alerting on the right axis needs a stable vocabulary.

## Decision

**Six-way `Outcome` enum + orthogonal `Severity` enum. Every DDB row and CW metric carries both.**

### `Outcome` — what happened (mutually exclusive)

| Value | Meaning | Common cause |
|---|---|---|
| `succeeded` | Ran to completion, no violations vs baseline. | Healthy tick. |
| `succeeded_with_violations` | Ran to completion, analyser flagged at least one violation. | Real drift / bias signal. |
| `skipped_no_input` | Container started but required input(s) absent. Not a bug. | No inference traffic in the window; missing optional baseline. |
| `skipped_insufficient_data` | Inputs present but below configured minimum sample size. | Low-volume tenant. |
| `failed_handled` | Analyser code raised an exception it recognises (bad config, incompatible schema, known upstream shape drift). `failure.json` written; not a crash. | Config drift, feature-list mismatch. |
| `failed_unhandled` | Uncaught exception. Base image's top-level `try/except` catches it, writes `failure.json`, exits non-zero. | Actual bug — file a ticket. |

**Rules:**
- Every row has exactly one `Outcome`.
- Base image writes `outcome=failed_unhandled` from its top-level handler. Analyser code never sets that value directly.
- Analyser code returns `AnalyserOutput(outcome=...)` for the first five values. Base validates that `failed_unhandled` is not returned by an analyser (it's base-only).

### `Severity` — what should the operator do (orthogonal)

| Value | Meaning | Notifier action |
|---|---|---|
| `info` | Nothing to see. | No alert. Archive only. |
| `warn` | Worth watching, not urgent. | No alert. Included in weekly digest. |
| `alert` | Human should look now. | Notifier fires. |

**Outcome → default severity mapping** (analyser can override where semantically justified):

| Outcome | Default severity |
|---|---|
| `succeeded` | `info` |
| `succeeded_with_violations` | `warn` (or `alert` if a critical threshold is crossed — analyser decides) |
| `skipped_no_input` | `info` |
| `skipped_insufficient_data` | `warn` |
| `failed_handled` | `warn` |
| `failed_unhandled` | `alert` |

### Notifier filter

EventBridge Pipes filter on the DDB Stream (matches DDB attribute `severity` under `dynamodb.NewImage`):

```json
{ "dynamodb": { "NewImage": { "severity": { "S": ["alert"] } } } }
```

Nothing else fires the notifier Lambda. `warn` + `info` go to the archive path only.

### Archive path

**Every** DDB Stream event → archive Pipe → S3 object at `s3://<archive-bucket>/execution-outcomes-archive/dt=<YYYY-MM-DD>/hour=<HH>/<event_id>.json`. No filter. This is the audit log.

### `failure.json` sidecar shape

Written to `<OUTPUT_URI>/failure.json` by the base image on any `failed_*` outcome:

```json
{
  "schema_version": "1.0",
  "outcome": "failed_unhandled",
  "exception_class": "KeyError",
  "message": "'label' not in inputs",
  "traceback": "Traceback (most recent call last):\n  File ...",
  "image_digest": "sha256:abc123...",
  "git_sha": "004e40f",
  "env_snapshot": { "PROJECT_NAME": "example-classifier", "ANALYSER_TYPE": "bias", "RUN_ID": "..." },
  "started_at": "2026-06-19T12:00:00Z",
  "failed_at": "2026-06-19T12:00:04Z"
}
```

Direct answer to the SageMaker Clarify pain of "container exited non-zero, log group empty, no explanation."

## Rationale

- Six values cover every real state we hit in `ml-iac`. Fewer values loses signal; more overfits.
- Separating **what happened** from **what to do** means a per-outcome default severity is a config, not a code path — easy to tune without rewrites.
- Notifier filter is one JSON expression, evaluated at EventBridge — Lambda only runs on real alerts. Cost + noise both low.
- `failure.json` sidecar was the biggest single lesson from `ml-iac`. Codified here so no analyser can skip it.

## Alternatives rejected

- **Two-state `Completed` / `Failed`.** Proven insufficient (`ml-iac` two-week debug that landed the six-way enum).
- **Severity from log level.** Log level is per-line; row severity is per-run. Different granularity.
- **Notifier reads full DDB row via Lambda logic.** More expensive, less transparent than an EventBridge filter expression. Filter fails closed if a new severity value slips in — a Lambda might mishandle it.

## Consequences

- Every analyser must return exactly one of the five analyser-set outcomes (base sets the sixth on crash).
- Adding a new outcome = major bump per `004` — the enum is part of the observability schema.
- Notifier filter is codified in the CDK; changes are code-reviewed alongside the outcome enum.
- `failure.json` is mandatory on any `failed_*` outcome. Base image writes it; analyser never has to remember.
