# Roadmap

> **Tracking note.** This roadmap is intentionally lightweight — flat markdown, no issues, no project board. When the scope grows past ~10 concurrent workstreams, switch to GitHub Issues + a project board and link them from here.

## Vision

A drop-in replacement for SageMaker Model Monitor + Clarify. Own the container. Own the math. Same output shape. No opaque errors. Multi-account-ready but single-account-compatible.

## Phase status

- [x] **Phase 0 — scaffolding.** Repo layout + assessment doc + design doc + standards + architecture + roadmap.
- [ ] **Phase 1 — interfaces (RED tests only).** Write the ABCs, config schemas, and failing tests. No implementation yet.
- [ ] **Phase 2 — baseline bias.** Implement `smclarify` wrapper. Numerical parity with Clarify on the UCI Adult fixture.
- [ ] **Phase 3 — baseline explainability.** SHAP wrapper. Model adapters for sklearn + XGBoost + PyTorch. Prove on Adult + a synthetic multiclass fixture.
- [ ] **Phase 4 — monitor container.** Extract from the sprint repo. De-brand. Publish.
- [ ] **Phase 5 — CDK library.** Extract constructs from the sprint repo. Multi-account posture in code. Publish.
- [ ] **Phase 6 — end-to-end example.** Public model + public dataset. Screenshot in README.
- [ ] **Phase 7 — internal consumption.** Sprint repo imports `cdk/`; deprecates inline stacks.

## Current phase: **Phase 1 — Interfaces**

### Goals

1. Define the shape of every public interface without implementing them.
2. Every interface has failing tests describing its contract.
3. Nothing merged unless the RED test is in place first (TDD, per `STANDARDS.md`).

### Task list

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

### Exit criteria for Phase 1

- All 10 tasks done.
- `uv run pytest` shows N failing tests (all expected — they're the RED that Phase 2 will turn GREEN).
- No production code in `containers/baseline/src/`. Only interfaces + docstrings + `raise NotImplementedError`.
- Every public symbol has a docstring.
- CI configured but expected to fail on tests (that's the point — we haven't implemented yet).

### Non-goals in Phase 1

- Container Dockerfile — Phase 2.
- CDK construct implementation — Phase 5.
- Any monitor-container work — Phase 4.
- Docs polish beyond what already exists.

## Phase 2 preview

Turn Phase 1's RED tests GREEN, starting with bias:
- `smclarify` wrapper (`compute_bias(df, spec) -> AnalysisReport`).
- Numerical parity test: emit `analysis.json` for UCI Adult, compare metrics to values in the Clarify docs.
- Container Dockerfile + entrypoint that reads env vars, resolves config, runs the analyzer, writes S3.
- Local reproduce script (`scripts/run-local.sh`).

## Governance

- **Sprint pressure.** This repo does not have any. If a task requires urgency, it belongs in the deploy repo, not here.
- **Contribution.** Anyone with repo access can open a draft PR. Merges require the `STANDARDS.md` checklist to be green.
- **Cross-repo dependency.** The deploy repo does not depend on this repo yet. Once Phase 7 lands, the deploy repo pins a version of `cdk/` and consumes ECR images from this repo's registry.
