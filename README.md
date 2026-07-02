# model-monitor-custom

Custom SageMaker Model Monitor replacement — containers + CDK constructs — designed and prototyped as an isolated R&D repo.

## Status

**Phase 0 — scaffolding.** Docs in place. No code yet. See [`docs/`](docs/).

## Why this repo exists

SageMaker Model Monitor + Clarify have proven expensive to ship in production:
- Closed-source containers with opaque error messages.
- Undocumented input shape requirements (JSONL nesting, JMESPath quirks, `content_template` placeholders).
- Hard AWS platform rate limits on `CreateProcessingJob` (1/sec, non-adjustable) that break under retry storms.
- Cross-account topologies (baselines in one account, model artefacts in another) require several bespoke IAM/KMS/S3 grants.
- AWS's own Clarify OSS repo (`aws/amazon-sagemaker-clarify`) had one substantive PR in the last 24 months.

Every other production ML monitoring stack in public writing — Netflix, Uber, Airbnb, DoorDash, WhyLabs, Aporia, Fiddler, Arize, Evidently — uses **custom containers on their own compute** with `shap` + in-house drift math, not Model Monitor.

This repo is the extraction of that pattern into a reusable, standalone project. See [`docs/SM_MODEL_MONITOR_ASSESSMENT.md`](docs/SM_MODEL_MONITOR_ASSESSMENT.md) for the full case.

## Layout

```
containers/
  monitor/    — hourly drift + bias + explain + shadow analyzers (existing, to migrate in)
  baseline/   — one-shot baseline compute (new, replaces Clarify Processing Job)
cdk/          — CDK library the deploy repo consumes (SFN + EventBridge + Processing Job orchestration)
docs/
  SM_MODEL_MONITOR_ASSESSMENT.md    — honest assessment: why move off Clarify (evidence-backed)
  BASELINE_CONTAINER_DESIGN.md  — design spec for the new baseline container
scripts/      — local-run + parity-check helpers
tests/        — unit + integration tests, fixture datasets
```

## Roadmap

- **Phase 0 (now):** repo + docs + scaffolding.
- **Phase 1:** import the `monitor/` container from the existing sprint repo — source, tests, ECR pipeline.
- **Phase 2:** build the `baseline/` container per `BASELINE_CONTAINER_DESIGN.md`. Prove numerical parity with Clarify on a pilot dataset.
- **Phase 3:** extract CDK constructs from the deploy repo into `cdk/` as a consumable library.
- **Phase 4:** deploy repo swaps its inline stacks for imports from this repo.
- **Phase 5:** rip Clarify Processing Job usage out of the deploy repo.

## Relationship to the deploy repo

- The deploy repo remains the deploy surface for real AWS accounts. This repo does not deploy anything.
- Once Phase 4 lands, the deploy repo becomes a thin consumer that imports:
  - `cdk/` — as a Python package (git URL or CodeArtifact)
  - `containers/*` — as ECR images published from this repo's CI
- Until then, sprint work stays in the deploy repo and R&D happens here.

## Contributing

R&D repo. No CI gates yet. No sprint deadlines. Draft freely.
