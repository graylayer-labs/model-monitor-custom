# 006 — Observability contract

**Status:** Accepted.
**Related:** [`002-container-base.md`](002-container-base.md), [`007-failure-taxonomy.md`](007-failure-taxonomy.md), [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## Context

Every analyser writes to CloudWatch Metrics, CloudWatch Logs, and DynamoDB. Dashboards, alarms, notifier filter, and archive Lambda all depend on **stable names and shapes**. Without a contract each analyser drifts; dashboards break silently.

## Decision

**One contract, enforced by the base image. Analysers never touch AWS SDKs — they return structured objects, base image emits.**

### CloudWatch Metrics

**Namespace:** `mmc/analyser/v1`. Major-version-suffixed so a v2 metric shape is a new namespace, not a mutation.

**Standard dimensions (every metric):**

| Dim | Meaning | Example |
|---|---|---|
| `Project` | Project slug from `PROJECT_NAME` | `example-classifier` |
| `Environment` | `test` / `prod` / `dev` | `test` |
| `AnalyserType` | `mq` / `dq` / `bias` / `explain` / `shadow` | `bias` |
| `Variant` | `AllTraffic` / shadow variant name | `AllTraffic` |

**Standard metrics (every run):**

| Name | Unit | Meaning |
|---|---|---|
| `RunCount` | Count | 1 per run. Sum → total runs. |
| `RunDurationSeconds` | Seconds | Wall time from container start to output write. |
| `ViolationCount` | Count | Analyser-reported violations vs baseline. |
| `Severity` | None | 0=info, 1=warn, 2=alert. See `007`. |

**Analyser-specific metrics** are namespaced by a further dimension `MetricName` inside the analyser's own emit — e.g. bias emits `MetricValue` with dims + `{MetricName: DPL|CDDL|KL|JS|...}`. Keeps namespace count low.

**Emit rules:**
- Base image emits **all** standard metrics automatically from the `AnalyserOutput` return.
- Analyser code must never call `boto3.client('cloudwatch')` directly.
- Timestamps: pass `run_started_at` in `AnalyserOutput`; base uses that (not `Date.now()`) so a re-run over historical data gets historical timestamps.

### DynamoDB outcomes table

**Table:** `mmc-<env>-outcomes` (per `001-iac-layout.md` naming).

**Keys:**
- Partition key: `run_id` (string, UUID)
- Sort key: `analyser_type` (string enum)

**Attributes (every row):**

| Attr | Type | Notes |
|---|---|---|
| `run_id` | S | UUID, matches SFN execution name |
| `analyser_type` | S | `mq` / `dq` / `bias` / `explain` / `shadow` |
| `project` | S | |
| `environment` | S | |
| `variant` | S | |
| `outcome` | S | Six-way enum (see `007`) |
| `severity` | S | `info` / `warn` / `alert` |
| `violation_count` | N | |
| `started_at` | S | ISO-8601 UTC |
| `ended_at` | S | ISO-8601 UTC |
| `duration_seconds` | N | |
| `image_digest` | S | Container image content-addressed digest |
| `git_sha` | S | Source commit that built the image |
| `result_s3_uri` | S | `s3://.../output/<analyser>/result.json` |
| `failure_s3_uri` | S | Present only when `outcome` is a failure. `s3://.../output/<analyser>/failure.json` |
| `notified` | S | Set by notifier on first alert emit; idempotency guard. Absent on non-alert rows. |

**Streams enabled** with `NEW_IMAGE` view. EventBridge Pipes fan out from the stream to alerting + archive consumers (per `ARCHITECTURE.md`).

**Idempotency:** notifier writes `notified: <ISO>` with a `ConditionExpression: attribute_not_exists(notified)`. Duplicate stream events are no-ops.

### Structured logging

- **Powertools `Logger`** singleton in base image. One log line = one JSON object to stdout.
- **Every log line** carries: `run_id`, `analyser_type`, `project`, `environment`, `variant`, plus record-specific fields.
- **Log levels:** `INFO` for lifecycle events (start, fetched inputs, wrote outputs), `WARN` for recoverable issues (missing optional input, retry), `ERROR` for the exception that triggers `failure.json`.
- **No `print()`** in production code. CI lint rule.
- **CloudWatch Log Groups:** `/mmc/<env>/<analyser>` — one per analyser × env. Retention 30 days default (overridable by CDK prop).

### CloudWatch dashboard convention

- **One dashboard per environment**: `mmc-<env>-overview`.
- **Widget order:** Completeness → Severity histogram → per-analyser RunCount+Duration → per-analyser violations → recent failures (log query widget filtered on `severity=alert`).
- **SEARCH expressions** for per-analyser widgets so new analysers appear automatically without dashboard code changes.

### What the base image does NOT emit

- Container-level CPU / memory / disk — v2 Lambda: from AWS/Lambda namespace (dims `FunctionName`); v1 ECS: from AWS/ECS namespace (dims `ClusterName` + `ServiceName` + `TaskDefinitionFamily`). Base makes no attempt to emit these.

## Rationale

- Stable names = stable dashboards + alarms across analyser additions.
- Base-only emit path = analyser code has zero AWS SDK surface, trivially unit-testable.
- Six-way outcome enum + `severity` + `violation_count` is enough for every alerting rule we've built to date in `ml-iac`.
- `run_id` as PK keeps a full run's rows co-located; single Query returns everything about one execution.

## Alternatives rejected

- **`execution_arn` as PK** (ml-iac shape). Coupled to SFN. `run_id` is portable — a local `docker run` also has a UUID, hits the same table shape in dev.
- **Separate table per analyser.** More overhead, no upside — analyser is a sort key.
- **OpenTelemetry from day 1.** Overkill until we have a real reason to leave CW. Base image can be extended to emit OTLP later; contract shape doesn't change.
- **Analysers emit directly.** SDK sprawl across 5 images. Contract drift. Rejected.

## Consequences

- Every new analyser gets standard metrics + DDB row for free by returning `AnalyserOutput`.
- Namespace / dim / attr renames are breaking changes. Bump `mmc/analyser/v2` + follow `004` dual-emit rules.
- `notified` attribute means the notifier's "did I already alert?" question is one DDB conditional write. No idempotency table needed.
