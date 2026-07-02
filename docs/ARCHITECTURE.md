# Architecture

## Diagrams

### Account topology

![Account topology](../blob/main/docs/diagrams/accounts.png?raw=true)

Source: [`docs/diagrams/accounts.d2`](diagrams/accounts.d2). Rendered with [D2](https://d2lang.com):

```bash
d2 --layout=elk docs/diagrams/accounts.d2 docs/diagrams/accounts.png
```

### Data flow

![Data flow](../blob/main/docs/diagrams/data-flow.png?raw=true)

Source: [`docs/diagrams/data-flow.mmd`](diagrams/data-flow.mmd). Rendered with Mermaid via [`ufx-mermaid`](https://github.com/EoinMcUF/ufx-mermaid):

```bash
~/.claude/skills/ufx-mermaid/render.sh docs/diagrams/data-flow.mmd docs/diagrams/data-flow.png
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

**Analyzers (mirroring what already exists in the sprint repo):**
- Model Quality — accuracy, F1, per-class metrics, confusion matrix.
- Data Quality — distribution drift on features, cardinality, null counts.
- Bias — drift of `analysis.json` bias metrics vs baseline.
- Explainability — surrogate feature importance vs baseline SHAP.
- Shadow — production variant vs shadow variant comparison.

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
