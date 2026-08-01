# 001 — IaC layout

**Status:** Accepted.
**Related research:** [`../research/IAC_DESIGN_RESEARCH.md`](../research/IAC_DESIGN_RESEARCH.md).

## Context

Multi-account CDK Python app. Needs to be single-account-collapsible for prototype but multi-account-ready. Must not couple to any deploy tool that requires team-size 5+ to justify.

## Decision

Four stacks, local `cdk deploy --profile` for prototype, mono-repo layout. See below.

## Stack layout

Four stacks. Each takes account IDs + role ARNs as inputs — never reads current account.

| Stack | Home account | Purpose |
|---|---|---|
| `ArtifactStack` | `ml-artifact` | ECR repos (`mmc/<container>`), baselines S3 bucket, KMS key. Cross-account grants published as outputs. |
| `SharedIamStack` | `ml-artifact` | Cross-account roles: `ml-operations` writes baselines; each `ml-inference-*` reads baselines. |
| `OperationsBaselineStack` | `ml-operations` | Snapshot analysis: EventBridge S3 rule → SFN Standard → Lambda Parallel branches (v2) / ECS Fargate (v1 design) → S3 output. v2 Update (2026-08-01): Swapped to Lambda for LocalStack-friendly testing without AWS credentials; ECS available as fallback. |
| `InferenceMonitorStack` | each `ml-inference-*` | Live analysis: EventBridge Scheduler → SFN Standard → Lambda Parallel branches (v2) / ECS Fargate (v1 design) → CW + DDB. v2 Update: Lambda reduces cold-start latency for episodic monitoring. DDB Streams → Pipes fan-out. |

`PipelineStack` (CDK Pipelines) deferred until team-size 5+ or first prod deploy.

## Deploy tool

- **Prototype (now):** local `cdk deploy --profile <acct> -c target_account=<name>` per stack. No pipeline.
- **When second engineer joins or prod deploy needed:** GitHub Actions matrix + OIDC role per account. Still no CDK Pipelines.
- **CDK Pipelines:** deferred. Adds bootstrap complexity that a 1-2 person team pays for without benefit.

## Repo shape

Mono-repo — `containers/`, `cdk/`, `shared/`, `docs/`, `tests/` in one tree. Split trigger (any one):
- Container count > 5
- Team split (containers team ≠ infra team)
- `cdk synth` > 2 min

## Naming

| Thing | Pattern | Example |
|---|---|---|
| Stack | `MMC-<Env>-<Component>` | `MMC-Test-InferenceMonitor` |
| ECR repo | `mmc/<container>` | `mmc/baseline`, `mmc/monitor` |
| S3 bucket | autogen (CDK) | let CDK pick — cross-account refs via stack output |
| SFN state machine | `mmc-<flow>-<env>` | `mmc-snapshot-test` |
| DDB table | `mmc-<env>-outcomes` | `mmc-test-outcomes` |

## Tags

Applied at app level via `Tags.of(app).add(...)`. Per-stack override for `Component`.

| Tag | Value |
|---|---|
| `Project` | `model-monitor-custom` |
| `Environment` | `test` \| `prod` \| `dev` |
| `Owner` | GH handle or team |
| `CostCenter` | `ml-platform` |
| `Component` | `artifact` \| `iam` \| `baseline` \| `monitor` (per-stack) |
| `ManagedBy` | `cdk` |

## Phase 2 build order

1. **`ArtifactStack`** — empty ECR repos, baselines bucket, KMS key. No cross-account grants yet.
2. **`SharedIamStack`** — cross-account roles referencing ArtifactStack outputs.
3. **`InferenceMonitorStack`** — full runtime shape with **busybox placeholder image** in ECR. Prove SFN → Lambda (v2) or Fargate (v1) → S3/DDB wiring end-to-end before any analyser code exists. v2 Update: Lambda container-image functions use same image tags as v1, wiring strategy remains similar.
4. **First container skeleton** — `containers/baseline/` implementing the env-var contract from `ARCHITECTURE.md`. Push real image to ECR, redeploy stack, verify same wiring picks up the new image.
5. **`OperationsBaselineStack`** — mirror of InferenceMonitorStack for the snapshot flow.

Container code targets real ECR + real SFN from day one of Phase 2 — no local-only detour.

## Cross-account posture

Every construct takes:
- Account IDs as `str` (never `self.account`)
- Cross-account role ARNs as inputs
- Cross-account bucket / KMS refs via `Bucket.from_bucket_attributes` etc.

Single-account collapse for a first deploy is a config choice — point every input at the same account ID. Code shape is identical.
