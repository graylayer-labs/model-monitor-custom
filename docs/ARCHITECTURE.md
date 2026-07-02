# Architecture

## Mental model — read this first

The system has **two runtime paths** on top of a shared **baselines bucket**.

### 1. Baseline compute (one-shot, per model version)

- **Trigger:** upstream training pipeline writes a training snapshot + analyzer config to the baselines bucket in `ml-artifact` (S3).
- **Job:** the [`baseline`](../containers/baseline/) container runs each enabled analyzer's baseline computation over the snapshot.
- **Output:** Clarify-compatible files back to the same bucket — `constraints.json` (MQ thresholds), `statistics.json` (DQ per-feature stats), `analysis.json` (pre-training bias metrics + SHAP feature importance). These files are the input for step 2.

### 2. Live monitoring (recurring, per endpoint)

- **Trigger:** cron schedule per inference endpoint (default hourly).
- **Job:** the [`monitor`](../containers/monitor/) container reads the current window's inference data capture (S3, local account), optional ground truth, and the **baseline output files** (cross-account read from `ml-artifact`).
- **Output:** CloudWatch metrics + DynamoDB outcome rows per analyzer per run. Consumers fan out from DDB.

### The five analyzers

| Analyzer | Needs baseline file | Live input |
|---|---|---|
| **Model Quality (MQ)** | `constraints.json` (thresholds) | data capture + ground truth |
| **Data Quality (DQ)** | `statistics.json` (per-feature stats) | data capture |
| **Bias** | `analysis.json` (pre-training bias metrics) | data capture (recompute + compare) |
| **Explainability** | `analysis.json` (SHAP feature importances) | data capture (recompute + compare) |
| **Shadow** | *none* — no baseline compute | shadow-variant capture **+** prod-variant capture (live-vs-live) |

**Shadow is the special case.** It is not a baseline-vs-live comparison; it's a live-vs-live comparison of two model variants deployed to the same endpoint. No baseline job needed — only the monitor job, and only when a shadow variant is configured on the endpoint.

So the runtime has two shapes:
- **Baseline-dependent analyzers** (MQ, DQ, Bias, Explainability) — need step 1 to produce their file, then step 2 consumes it.
- **Baseline-independent analyzer** (Shadow) — only step 2, and only when the endpoint has a shadow variant.

## System boundaries — three decoupled subsystems

Three subsystems, coupled by **published JSON schemas** (not code).

```
[ Producer ]  →  input/  →  [ Snapshot analysis ]  →  output/  →  [ Live analysis ]  →  CW / DDB
    ↑              ↑                                    ↑
   any            config.json                          result.json
   producer       snapshot.jsonl                       (published schema)
                  (published schema)
```

- **Producer** — training pipeline, notebook, manual `aws s3 cp` — anything that can write to S3. Not our concern how it gets there.
- **Snapshot analysis** — reads `input/`, computes per-analyser results, writes `output/`.
- **Live analysis** — reads `output/`, diffs against current-window inference data, emits CloudWatch metrics + DynamoDB rows.

Each subsystem owns **one output schema**. Downstream consumers depend on the schema, not on the code. Schema versions are treated like any other API — breaking changes get a version bump.

**Published schemas (in `shared/`):**

| Schema | Producer | Consumer |
|---|---|---|
| `snapshot-input-schema.json` | any producer (typically train pipeline) | snapshot analysers |
| `snapshot-output-schema.json` | snapshot analysers | live analysers |
| `live-output-schema.json` | live analysers | CloudWatch / DynamoDB — external observability |

**Optional Python helper** `monitor-publish` (also in `shared/`) — wraps schema validation + S3 write for producers who want convenience. Not required — producers can bypass and do raw S3 puts as long as the output validates against the schema.

**S3 layout (contract, not implementation detail):**

```
s3://baselines-bucket/<project>/v<N>/
├── input/
│   ├── config.json      monitoring config (schema: snapshot-input)
│   ├── snapshot.jsonl   training-time data snapshot
│   └── (predictions.jsonl)  optional — for MQ if predictions available
├── output/              snapshot analysers write here
│   ├── mq/result.json           (schema: snapshot-output)
│   ├── dq/result.json
│   ├── bias/result.json
│   ├── explain/result.json
│   ├── {analyser}/_provenance.json
│   └── {analyser}/failure.json  (only on error)
└── _provenance.json     emitted by producer — image digest, git SHA, timestamps
```

Immutable per `<N>`. Never overwrite a version — new state = new directory.

## Container I/O contract

Every analyser container — snapshot or live, all five kinds — implements the **same input/output protocol**. The contract lives in the `shared/` package and is enforced by Pydantic at container startup.

Grounded in the SFN research: teams overwhelmingly launch containers with a small structured env-var payload and let the container fetch anything larger itself (research doc: `SFN_STATE_STRUCTURE_RESEARCH.md`).

### Inputs — what SFN passes as `ContainerOverrides.Environment`

| Env var | Type | Meaning |
|---|---|---|
| `PROJECT_NAME` | str | Project slug, e.g. `example-classifier`. |
| `RUN_ID` | str | UUID / SFN execution name. Correlates all outputs of this run. |
| `ANALYSER_TYPE` | enum | `mq` \| `dq` \| `bias` \| `explain` \| `shadow`. |
| `INPUT_URIS_JSON` | JSON str | `{"snapshot": "s3://…", "capture": "s3://…", "gt": "s3://…", "model": "s3://…"}` — nulls omitted. |
| `OUTPUT_URI` | str | `s3://…` prefix where the container writes results. |
| `CONFIG_URI` | str | `s3://…/config.json` — full project config as a versioned S3 object. |

**Total payload well under 8 KB** (ECS `ContainerOverrides` limit). No SSM Parameter Store in the runtime path — config lives in S3 as a versioned JSON file, KMS-encrypted, IAM'd. Simpler + trivially reproducible locally.

### Outputs — what the container writes

| Path | Contents |
|---|---|
| `OUTPUT_URI/result.json` | Pydantic-validated `AnalyserOutput` — the analyser's actual result. |
| `OUTPUT_URI/_provenance.json` | Container image digest, git SHA, timestamps, `run_id`. |
| `OUTPUT_URI/failure.json` | Only on error. Structured exception + full traceback. |

Plus (direct AWS SDK from container, own IAM):

- **CloudWatch Metrics** — per-analyser namespace, dims `PackageGroupName · SiloId · MonitorType · Variant`.
- **DynamoDB outcome row** — one per (execution, analyser).
- **CloudWatch Logs** — automatic via ECS task log driver.

### Failure semantics

Each SFN branch wraps its container task in:

- **Retry** on `States.TaskFailed` / `ECS.AmazonECSException` — max 3 attempts, exponential backoff.
- **Catch** on `States.ALL` → routes to a `MarkBranchFailed` state that writes a DDB failure marker.

This satisfies the "one branch dying does not kill siblings" requirement (SFN research §7).

### Why S3 for config, not SSM

Real-world reference architectures ([SFN_STATE_STRUCTURE_RESEARCH.md](SFN_STATE_STRUCTURE_RESEARCH.md)) overwhelmingly use S3 for structured config that a container needs to fetch. Reasons:

- One IAM story — the container already needs S3 for input/output.
- Versioned — S3 versioning gives free config-history.
- Diffable — `aws s3 cp s3://…/config.json - | jq` beats `aws ssm get-parameter-history`.
- Local dev — a real file in a real bucket beats "mock SSM".
- No extra service call — the container reads all its inputs from S3 in one go.

SSM Parameter Store remains available for deploy-time / infra config; it is not on the runtime hot path.

### Local reproduction

Same env-var contract, same S3 fetches:

```bash
docker run --rm \
  -e PROJECT_NAME=example-classifier \
  -e RUN_ID=local-dev-1 \
  -e ANALYSER_TYPE=bias \
  -e INPUT_URIS_JSON='{"snapshot":"s3://.../snapshot.jsonl"}' \
  -e OUTPUT_URI=s3://.../out/local-dev-1/bias \
  -e CONFIG_URI=s3://.../config.json \
  -e AWS_PROFILE=dev \
  analyser-bias:latest
```

No SFN, no ECS, no CDK — same container, same result.

## Diagrams

### Account topology

![Account topology](../blob/main/docs/diagrams/accounts.png?raw=true)

Source: [`docs/diagrams/accounts.d2`](diagrams/accounts.d2). Rendered with [D2](https://d2lang.com):

```bash
d2 --layout=elk docs/diagrams/accounts.d2 docs/diagrams/accounts.png
```

### Snapshot analysis (one-shot, per model version)

![Snapshot analysis](../blob/main/docs/diagrams/snapshot-analysis.png?raw=true)

Source: [`docs/diagrams/snapshot-analysis.mmd`](diagrams/snapshot-analysis.mmd).

### Live analysis (recurring, per endpoint)

![Live analysis](../blob/main/docs/diagrams/live-analysis.png?raw=true)

Source: [`docs/diagrams/live-analysis.mmd`](diagrams/live-analysis.mmd).

Both mermaid diagrams rendered via [`ufx-mermaid`](https://github.com/EoinMcUF/ufx-mermaid):

```bash
~/.claude/skills/ufx-mermaid/render.sh docs/diagrams/<file>.mmd docs/diagrams/<file>.png
```

## Overview

`model-monitor-custom` provides two containers and a CDK library that together replace SageMaker Model Monitor + Clarify:

1. **`containers/baseline`** — one-shot Processing Job that reads a training snapshot + config from S3 and emits `analysis.json` (bias metrics + SHAP feature importance) to S3. Replaces the SageMaker Clarify Processing Job.
2. **`containers/monitor`** — recurring Processing Job that reads inference data-capture + baseline artefacts, computes drift, and emits CloudWatch metrics + DDB outcome rows. Replaces the SageMaker Model Monitor container.
3. **`cdk/`** — CDK constructs that wire both containers into Step Functions, EventBridge, S3, IAM, and CloudWatch. Consumed by a deploy repo (real AWS accounts) or by the `examples/` for local demos.

## Assumed AWS account topology

The project targets the AWS-recommended multi-account layout for ML workloads. Even when a prototype deploys everything to a single account, the code takes account IDs and role ARNs as inputs so switching to multi-account is a config change, not a rewrite.

```
ML Domain
├── ml-artifact           Central artefact store — shared across environments.
│                         Owns: model.tar.gz S3 bucket, monitoring-baselines S3
│                         bucket, ModelPackageGroup (registry).
│                         Publishes cross-account grants to inference planes.
│
├── ml-operations         Training + baseline compute account.
│                         Owns: training pipelines, baseline Processing Jobs,
│                         GitHub Actions role for CI-driven deploys.
│                         Writes baselines up to ml-artifact.
│
├── (ml-data-platform — upstream, out of scope)      Raw data + ML-ready datasets.
│                         Upstream of this project — we do not touch it.
│
├── ml-dev                Sandbox: SageMaker Studio Lab, dev notebooks.
│                         Identity Center users have permission here.
│
└── Inference Plane
    ├── ml-inference-test    Test-tier endpoints (per tenant).
    │                        Runs the monitor container as a Processing Job.
    │                        Emits CW metrics + DDB outcomes.
    │
    └── ml-inference-prod    Prod-tier endpoints (per tenant).
                             Same shape as test. Endpoint per tenant.
```

### Which components live in which account

| Component | Home account | Cross-account concerns |
|---|---|---|
| Model artefact (`model.tar.gz`) | `ml-artifact` (S3) | inference planes need `s3:GetObject` + `kms:Decrypt` |
| ModelPackageGroup | `ml-artifact` (SageMaker) | inference planes need `sagemaker:DescribeModelPackage`, `sagemaker:CreateModel` (RAM share or resource policy) |
| Monitoring baselines bucket | `ml-artifact` (S3) | `ml-operations` needs `s3:PutObject`; inference planes need `s3:GetObject` |
| Baseline Processing Job | `ml-operations` (SageMaker) | needs cross-account read of model artefact + KMS decrypt from `ml-artifact` |
| Training pipeline | `ml-operations` (SageMaker) | writes baselines to `ml-artifact`; registers models in `ml-artifact` |
| Endpoint | `ml-inference-*` (SageMaker) | pulls model via cross-account `CreateModel` from `ml-artifact` |
| Monitor Processing Job | `ml-inference-*` (SageMaker) | reads baselines from `ml-artifact` (S3+KMS); writes outcomes to local DDB + CW |
| Backend consumers of predictions | separate accounts (`BACKEND-TEST`, `BACKEND-PROD-LP`) | invoke endpoints via a Silo Invoke Role |

### Prototype single-account collapse

For a first deploy, all seven components can live in one account (Data Science or ML Operations). Every CDK construct still requires account IDs + role ARNs as inputs — pointing them all at the same value is a valid but explicit choice.

## Component boundaries

### `containers/baseline`

**Trigger:** SageMaker Processing Job launched by a Step Functions state machine.

**Trigger source:** EventBridge rule on `s3:PutObject` for the training snapshot dataset — same pattern as SageMaker Clarify's original wiring, but the state machine is ours.

**Inputs (env vars + S3):**
- `PACKAGE_GROUP_NAME` — model package group name.
- `BASELINE_VERSION` — integer, monotonically increasing per baseline recompute.
- `MONITOR_TYPE` — `BIAS` or `EXPLAINABILITY`.
- `INPUT_S3_URI` — dataset (JSONL/Parquet).
- `CONFIG_S3_URI` — analysis config JSON (spec below).
- `MODEL_S3_URI` — `s3://.../model.tar.gz` (Explainability only).
- `OUTPUT_S3_URI` — where to write `analysis.json` + `failure.json`.

**Outputs (S3):**
- `analysis.json` — bias metrics + SHAP feature importance. Schema mirrors Clarify's output for drop-in downstream compatibility.
- `_provenance.json` — sidecar with container image digest, git SHA, timestamps.
- `failure.json` — sidecar written only on hard failure.

**Contract with the monitor container:** the monitor reads `analysis.json` from S3 at run time and computes drift against current-window inference data. Output shape stability is the contract.

### `containers/monitor`

**Trigger:** SageMaker Processing Job on a fixed schedule (default hourly).

**Inputs:**
- Data capture from inference endpoint (S3).
- Baseline artefacts from `ml-artifact` (S3, cross-account).
- Optional ground-truth stream.

**Analyzers** — see [Mental model → The five analyzers](#the-five-analyzers) for the full matrix of what each one needs. In brief:

- **Model Quality** — accuracy, F1, per-class metrics, confusion matrix. Needs `constraints.json` + ground truth.
- **Data Quality** — distribution drift on features, cardinality, null counts. Needs `statistics.json`.
- **Bias** — drift of pre-training bias metrics vs baseline. Needs `analysis.json`.
- **Explainability** — feature importance drift vs baseline SHAP values. Needs `analysis.json`.
- **Shadow** — live-vs-live comparison of production vs shadow variant on the same endpoint. **No baseline file** — only runs when a shadow variant is configured.

**Outputs:**
- CloudWatch metrics under namespace `<configurable>/monitoring/v2`.
- DDB rows into `<silo>-execution-outcomes` table.
- Per-outcome severity + violation counts.

### `cdk/`

Composable CDK v2 constructs published as a Python package.

**Layered constructs:**

- `AnalyzerBaselineConstruct` — SFN + EventBridge + Processing Job that launches the `baseline` container. Takes ECR image URI, role ARNs, S3 bucket references as inputs.
- `MonitoringScheduleConstruct` — SageMaker Model Monitor schedule (or plain EventBridge cron) that launches the `monitor` container.
- `OutcomesConstruct` — DDB table + streams + EventBridge Pipes fan-out.
- `NotifierConstruct` — reference Lambda that consumes outcomes and emits alerts. Ship as example; users bring their own.

Each construct takes account IDs and role ARNs as parameters. No hardcoded account references anywhere in `cdk/`.

## Data flow

```
[ml-operations]                                [ml-artifact]                       [ml-inference-*]
─────────────────                              ─────────────                       ────────────────
Training pipeline                              Monitoring baselines bucket        Endpoint (per tenant)
        │                                              │  ▲                              │
        │ writes training snapshot ────────────────►   │  │                              │ produces
        │                                              │  │ analysis.json                │ data capture
        │                                              │  │ from baseline                │
        │                                              │  │                              ▼
        │ writes analysis_config.json ─────────────►   │  │                       Data capture (S3)
        │                                              │  │                              │
        │                                              ▼  │                              │
        │                                     EventBridge S3 rule                        │
        │                                              │                                 │
        │                                              ▼                                 │
        │                                    Baseline SFN + Processing Job               │
        │                                    (baseline container)                    │
        │                                              │ reads model artefact            │
        │                                              │ (cross-account)                 │
        │                                              ▼                                 │
        │                                    Writes analysis.json ────────────►          │
        │                                                                                │
        │                                                                                ▼
        │                                    baselines (cross-account S3)      Monitor Processing Job
        │                                    ────────────────────────────────► (monitor container)
        │                                                                                │
        │                                                                                ▼
        │                                                                        CW metrics + DDB rows
```

Every arrow crossing an account boundary is an explicit IAM grant + optional KMS grant + optional bucket policy statement. `cdk/` provides opt-in helpers that emit those statements when the caller specifies a cross-account topology.

## Non-goals

- **Not a SaaS.** No hosted UI, no vendor backend. Everything runs in your accounts.
- **Not a replacement for `evidently`, `whylogs`, `Arize`, `Fiddler`, etc.** These are alternatives. This project is the "own it end-to-end on SageMaker Processing Jobs" option.
- **Not tied to any specific model type.** Users bring a `ModelAdapter` (see `BASELINE_CONTAINER_DESIGN.md`).
- **Not tied to any specific dashboard tool.** CW is one option; users can point Pipes at Datadog / Grafana / anywhere.
