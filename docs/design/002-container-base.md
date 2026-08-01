# 002 — Container base image + analyser pattern

**Status:** Accepted.
**v2 Update (2026-08-01):** This design remains unchanged. Container base image + contract is compute-agnostic — works identically whether deployed as Lambda (v2 default) or ECS Fargate (v1 option). See `001-iac-layout.md` for compute backend selection.
**Related:** [`001-iac-layout.md`](001-iac-layout.md), [`003-anti-sagemaker-guardrails.md`](003-anti-sagemaker-guardrails.md), [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## Context

Five analysers (MQ, DQ, Bias, Explain, Shadow) plus one baseline flow share the exact same I/O contract from `ARCHITECTURE.md`: read env vars, fetch S3 inputs, run analyser, write `result.json` + `_provenance.json` (+ `failure.json` on error), emit CW metrics + DDB row.

Without a base image every analyser reimplements the contract → drift, inconsistency, and the SageMaker-shaped assumptions creep back in.

## Decision

**One base image (`mmc/analyser-base`) owns the contract. Each analyser image extends it and owns only its math.**

### Layer split

```
mmc/analyser-base
├── Python 3.12 + uv
├── shared/ package (schemas, contract Pydantic models, S3/DDB/CW clients)
├── entrypoint.py — main() — reads env-var contract, dispatches to analyser, writes outputs, handles failure sidecar
├── structured JSON logging (Powertools Logger)
├── SageMaker ban-list guard (see 003)
└── contract test harness — reusable pytest fixtures

mmc/analyser-bias, mmc/analyser-dq, mmc/analyser-mq,
mmc/analyser-explain, mmc/analyser-shadow
├── FROM mmc/analyser-base:sha-<pinned>
└── implements Analyser Protocol → compute(inputs, config) → AnalyserOutput
```

### Base owns

- Env-var parse (`PROJECT_NAME`, `RUN_ID`, `ANALYSER_TYPE`, `INPUT_URIS_JSON`, `OUTPUT_URI`, `CONFIG_URI`) with Pydantic validation. Startup fails loud on missing.
- Config fetch from `CONFIG_URI`, validated against `snapshot-input-schema.json`.
- Input fetch — resolves S3 URIs, downloads to `/tmp/`, hands paths to analyser.
- Output write — takes `AnalyserOutput`, writes `result.json` + `_provenance.json` to `OUTPUT_URI/`.
- Top-level `try/except` → `failure.json` sidecar with exception class, message, traceback, image digest, env snapshot.
- CW metrics + DDB row emit — analyser returns structured output; base writes.
- Powertools structured JSON logging to stdout.

### Analyser owns

One class: `class BiasAnalyser(Analyser): def compute(self, inputs: AnalyserInputs, config: BiasSpec) -> AnalyserOutput`.

**Zero AWS SDK calls. Zero S3 fetches. Zero DDB writes. Pure Python — testable without moto/localstack.**

### Analyser Dockerfile pattern (4 lines)

```dockerfile
FROM 165….dkr.ecr.eu-west-1.amazonaws.com/mmc/analyser-base:sha-<pinned>
COPY pyproject.toml src/ ./
RUN uv pip install --system .
ENV MMC_ANALYSER_MODULE=analyser_bias.analyser:BiasAnalyser
```

Base's `entrypoint.py` does `importlib.import_module(module).cls()` — analyser image never touches entrypoint code.

## Repo shape

```
containers/
├── base/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── src/mmc_base/
│   │   ├── entrypoint.py
│   │   ├── contract.py            Pydantic input/output models
│   │   ├── io_s3.py
│   │   ├── io_ddb.py
│   │   ├── io_cw.py
│   │   ├── provenance.py
│   │   ├── failure.py
│   │   └── analyser.py            Analyser Protocol
│   └── tests/                     contract test harness
├── bias/  ── Dockerfile FROM base + src/analyser_bias/ + tests/
├── dq/    ── same shape
├── mq/    ── same shape
├── explain/── same shape
└── shadow/ ── same shape
```

## Version pinning

- Base image tagged `mmc/analyser-base:sha-<git sha>`.
- Analyser Dockerfiles pin a specific base SHA — **never `:latest`**.
- Bumping the base = deliberate PR updating every analyser's `FROM`. CI check enforces no `:latest`.

## Rationale

- Contract enforcement in one place — no drift across 5 analysers.
- Analysers become pure functions — trivially unit-testable.
- SageMaker-shaped assumptions can only creep in via the base — one place to guard (see 003).
- 5 analysers is the exact scale where a base pays for itself. At 1 it would be premature.

## Alternatives rejected

- **No base — each analyser standalone.** Contract drifts. Failure-sidecar semantics land differently in every image. Ban-list guard has to be reimplemented 5 times.
- **Runtime plugin loader (single image, analyser as pip install).** Cross-analyser dependency conflicts (e.g. shap vs smclarify pins). Image bloat. Rejected.
- **Framework like Kedro/Prefect/Metaflow.** Adds a runtime + concepts unrelated to our problem. YAGNI.

## Consequences

- **Rebuild fan-out** — bumping base rebuilds 5 analyser images. Pinned SHAs make it deliberate.
- **Local dev friction** — analyser dev needs base built locally first. Mitigated by `scripts/build-base.sh` + `docker compose` per analyser.
- Every ported analyser from `ml-core/containers/monitor/` must be reshaped to the `Analyser` Protocol before it lands — no lift-and-shift.

## Phase 2 impact

`ROADMAP.md` task 2.5 splits:

- **2.5a — `containers/base/`.** Contract, entrypoint, sidecar, ban-list guard, contract test harness. RED tests first.
- **2.5b — `containers/bias/`** (first consumer). Stub `NoopAnalyser` returning canned output. Proves the base + analyser pattern end-to-end. Real math is Phase 3.
