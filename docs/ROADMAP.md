# Roadmap

> **Tracking note.** This roadmap is intentionally lightweight — flat markdown, no issues, no project board. When the scope grows past ~10 concurrent workstreams, switch to GitHub Issues + a project board and link them from here.

## Vision

A drop-in replacement for SageMaker Model Monitor + Clarify. Own the container. Own the math. Same output shape. No opaque errors. Multi-account-ready but single-account-compatible.

## Phase status

- [x] **Phase 0 — scaffolding.** Repo layout + assessment doc + design doc + standards + architecture + roadmap.
- [x] **Phase 1 — interfaces.** ABCs, config schemas, Protocols, fixtures, CI skeleton.
- [ ] **Phase 2 — infra skeleton + first container.** CDK stacks 1-3 (`ArtifactStack`, `SharedIamStack`, `InferenceMonitorStack`) with busybox placeholder image. Prove SFN → Fargate → S3/DDB wiring end-to-end. Then first `containers/baseline/` skeleton implementing the env-var contract. Redeploy stack; verify real image picks up same wiring.
- [ ] **Phase 3 — baseline bias analyser.** `smclarify` wrapper. Numerical parity with Clarify on UCI Adult. `OperationsBaselineStack` mirrors InferenceMonitorStack for the snapshot flow.
- [ ] **Phase 4 — baseline explainability.** SHAP wrapper. Model adapters for sklearn + XGBoost + PyTorch. Prove on Adult + synthetic multiclass fixture.
- [ ] **Phase 5 — monitor container (5 analyzers).** MQ, DQ, Bias, Explainability, Shadow.
- [ ] **Phase 6 — end-to-end example.** Public model + public dataset. Screenshot in README.
- [ ] **Phase 7 — CDK Pipelines / GitHub Actions matrix.** Only when team-size 5+ or first prod deploy.

## Current phase: **Phase 2 — Infra skeleton + first container**

See [`design/001-iac-layout.md`](design/001-iac-layout.md) for the stack layout, naming, tags, and build order this phase implements. See [`design/002-container-base.md`](design/002-container-base.md) for the container base + analyser pattern, and [`design/003-anti-sagemaker-guardrails.md`](design/003-anti-sagemaker-guardrails.md) for the CI + runtime guardrails.

### Goals

1. Stand up `ArtifactStack`, `SharedIamStack`, `InferenceMonitorStack` with a busybox placeholder image.
2. Prove SFN Standard → ECS Fargate Parallel → S3/DDB wiring end-to-end before writing analyser code.
3. Build first `containers/baseline/` skeleton implementing the env-var contract from `ARCHITECTURE.md`. Push to ECR. Redeploy. Verify same wiring picks up the real image.
4. TDD throughout — RED test before production code (per `STANDARDS.md`).

### Task list

Build order (see `design/001-iac-layout.md` Phase 2 section):

- [ ] **2.1 — `ArtifactStack`** in `cdk/src/model_monitor_cdk/stacks/artifact_stack.py`. ECR repos `mmc/baseline` + `mmc/monitor`, baselines S3 bucket, KMS key. Account IDs as inputs, no `self.account`. Failing tests for stack synth + resource count.
- [ ] **2.2 — `SharedIamStack`** in `cdk/src/model_monitor_cdk/stacks/shared_iam_stack.py`. Cross-account role for `ml-operations` baseline write; per-`ml-inference-*` role for baseline read. Consumes `ArtifactStack` outputs. Failing tests for principal ARNs + scoped policies.
- [ ] **2.3 — `InferenceMonitorStack`** in `cdk/src/model_monitor_cdk/stacks/inference_monitor_stack.py`. EventBridge Scheduler cron → SFN Standard → ECS Fargate Parallel (5 branches) → CW + DDB. DDB Streams → EventBridge Pipes (alert + archive fan-out). Per-branch Retry/Catch. **Uses busybox image URI as input** — real ECR image later. Failing tests for state-machine shape + per-branch Catch presence.
- [ ] **2.4 — Placeholder image push.** `scripts/push-placeholder.sh` — `docker pull busybox:latest`, tag as `<acct>.dkr.ecr.eu-west-1.amazonaws.com/mmc/baseline:placeholder`, push. Redeploy `InferenceMonitorStack` pointed at this tag. Manual SFN start proves wiring.
- [ ] **2.5a — `containers/base/` (mmc/analyser-base).** Per `design/002-container-base.md`. Entrypoint, contract Pydantic models, S3/DDB/CW clients, provenance, failure sidecar, Analyser Protocol, SageMaker ban-list guard (per `design/003`). Contract test harness (reusable fixtures). RED tests first.
- [ ] **2.5b — `containers/bias/` skeleton with NoopAnalyser.** `FROM mmc/analyser-base:sha-<pinned>`. Implements `Analyser` Protocol returning a canned `AnalyserOutput`. Proves the base + analyser pattern end-to-end. Real bias math is Phase 3.
- [ ] **2.6 — Push real images + redeploy.** Push base + bias images to ECR. Redeploy `InferenceMonitorStack`. Verify same SFN wiring picks up the new images. End-to-end run writes `result.json` + `_provenance.json` to S3 + one DDB row per branch.
- [ ] **2.7 — Guardrails live in CI.** `.github/workflows/anti-sagemaker.yml` per `design/003` — grep-guard fails on `SM_MODEL_DIR|SM_CHANNEL|/opt/ml/|content_template|CreateProcessingJob|MonitoringExecution`. Runs on every PR.

### Prior phase (kept for reference — completed)

Phase 1 tasks (interface definitions, all RED tests in place):

- [x] **1.1 — `ModelAdapter` ABC.** `containers/baseline/src/model_baseline/adapters/base.py`. Abstract methods: `load(model_uri: str) -> None`, `predict_proba(features: pd.DataFrame) -> np.ndarray`, `feature_headers() -> list[str]`, `class_labels() -> list[str]`. Failing test that asserts subclass must implement all four.
- [x] **1.2 — `BaselineConfig` schema.** Pydantic model covering dataset URI, config URI, model URI, monitor type, output URI, thresholds. `extra="forbid"`. Failing tests for shape + required fields.
- [x] **1.3 — `BiasSpec` schema.** Sub-model: label column, positive-label values, list of `Facet(name, values)`, list of bias methods. Failing tests.
- [x] **1.4 — `ExplainSpec` schema.** Sub-model: SHAP num_samples, SHAP background size, agg_method. Failing tests.
- [x] **1.5 — `AnalysisReport` output schema.** Pydantic model that serialises to the Clarify-compatible `analysis.json`. Failing test asserting shape parity with a captured real Clarify output (fixture).
- [x] **1.6 — Analyzer protocols.** `BiasAnalyzer` and `ExplainabilityAnalyzer` protocols with a single `compute(config: BaselineConfig, adapter: ModelAdapter) -> AnalysisReport` method. Failing tests.
- [x] **1.7 — Container entrypoint contract.** `containers/baseline/src/model_baseline/cli.py` — env var parsing + config resolution. Failing tests over the env var → config transformation.
- [x] **1.8 — CDK construct props.** `cdk/src/model_monitor_cdk/constructs/analyzer_baseline.py::AnalyzerBaselineProps` dataclass. Fields: ECR image URI, execution role ARN, baselines bucket ref, target account ID, event source. Failing test that a construct instantiation validates props.
- [x] **1.9 — Fixture datasets.** `tests/fixtures/`:
  - `adult.parquet` — UCI Adult census (public bias tutorial dataset).
  - `synthetic_3class.parquet` — deterministic synthetic dataset for SHAP.
  - Each with a README explaining generation.
- [x] **1.10 — CI skeleton.** `.github/workflows/lint-and-test.yml` — runs `uv run ruff check`, `uv run ty check`, `uv run pytest`. All three must pass. Fails until Phase 2 implementations pass the RED tests.

### Exit criteria for Phase 2

- Three stacks deploy clean into a single account (prototype collapse OK — inputs still take account IDs, not `self.account`).
- Manual SFN start with busybox image completes all 5 Parallel branches without cross-branch failure.
- Baseline skeleton image pushed to real ECR, redeployed, one end-to-end run writes stub `result.json` + `_provenance.json` to S3 and one row per branch to DDB.
- `uv run pytest` green on new construct + container tests.
- No hardcoded account IDs anywhere in `cdk/`.

### Non-goals in Phase 2

- Real bias / SHAP / MQ / DQ logic — Phase 3+.
- Monitor container — Phase 5.
- CDK Pipelines / GH Actions matrix — Phase 7.
- Multi-account deploy (single-account collapse acceptable; code shape identical).

## Governance

- **Sprint pressure.** This repo does not have any. If a task requires urgency, it belongs in the deploy repo, not here.
- **Contribution.** Anyone with repo access can open a draft PR. Merges require the `STANDARDS.md` checklist to be green.
- **Cross-repo dependency.** The deploy repo does not depend on this repo yet. Once Phase 7 lands, the deploy repo pins a version of `cdk/` and consumes ECR images from this repo's registry.
