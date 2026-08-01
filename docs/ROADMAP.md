# Roadmap

> **Tracking note.** This roadmap is intentionally lightweight — flat markdown, no issues, no project board. When the scope grows past ~10 concurrent workstreams, switch to GitHub Issues + a project board and link them from here.

## Vision

A drop-in replacement for SageMaker Model Monitor + Clarify. Own the container. Own the math. Same output shape. No opaque errors. Multi-account-ready but single-account-compatible.

## Phase status

- [x] **Phase 0 — scaffolding.** Repo layout + assessment doc + design doc + standards + architecture + roadmap.
- [x] **Phase 1 — interfaces.** ABCs, config schemas, Protocols, fixtures, CI skeleton.
- [x] **Phase 2 — infra skeleton + first container.** CDK stacks 1-3 (`ArtifactStack`, `SharedIamStack`, `InferenceMonitorStack`) with Lambda as default compute backend. SFN → Lambda Parallel → S3/DDB wiring end-to-end. `containers/baseline/` skeleton implementing the env-var contract. Lambda-first architecture replaces original Fargate assumption; ECS available as config-driven fallback.
- [x] **Phase 3 — baseline bias analyser.** `smclarify` wrapper. Numerical parity with Clarify on UCI Adult. `OperationsBaselineStack` mirrors InferenceMonitorStack for the snapshot flow.
- [x] **Phase 4 — baseline explainability.** SHAP wrapper. Model adapters for sklearn + XGBoost + PyTorch. Prove on Adult + synthetic multiclass fixture.
- [x] **Phase 5 — monitor container (5 analyzers).** MQ, DQ, Bias, Explainability, Shadow.
- [x] **Phase 6 — end-to-end example.** Public model + public dataset. Screenshot in README.
- [ ] **Phase 7 — CDK Pipelines / GitHub Actions matrix.** Only when team-size 5+ or first prod deploy.

## Current phase: **Portfolio-readiness / v0.2.0 release**

Implementation phases (0–6) are complete. Current focus: finalize documentation, polish examples, and prepare for portfolio release. Phase 7 (CI/CD automation) deferred until team expansion or production deploy.

### What's done

Phases 2–6 deliver a working, production-ready infrastructure and analyzer framework:
- **Phase 2:** Full CDK infrastructure (Artifact, IAM, Inference stacks) with Lambda-first compute. SFN orchestrates 5 parallel analyzer branches. End-to-end tested via LocalStack.
- **Phase 3–6:** All core analyzers (baseline bias, explainability, monitoring, data quality) implemented with real Clarify/SHAP wrappers. Public example with UCI Adult dataset.

**Lambda-first architecture note:** v2 replaced the original Phase 2 design's Fargate assumption. Lambda is now the default compute backend with a config-driven ECS fallback, reducing operational complexity while maintaining the same wiring contract.

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
