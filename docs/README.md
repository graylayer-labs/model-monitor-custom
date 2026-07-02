# Docs

Start here.

## Core

| Doc | When to read |
|---|---|
| [`STANDARDS.md`](STANDARDS.md) | Before writing a line of code. Non-negotiables (TDD, ruff, ty). |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | The mental model, system boundaries, container I/O contract, S3 layout, AWS account topology. |
| [`IAC_DESIGN.md`](IAC_DESIGN.md) | CDK stack layout, deploy tool, naming, tags, Phase 2 build order. Decision digest. |
| [`ROADMAP.md`](ROADMAP.md) | Phase status + current-phase task list. |

## Background

| Doc | Why it exists |
|---|---|
| [`SM_MODEL_MONITOR_ASSESSMENT.md`](SM_MODEL_MONITOR_ASSESSMENT.md) | Evidence-backed case for why this project exists (moving off SageMaker Model Monitor + Clarify). |
| [`BASELINE_CONTAINER_DESIGN.md`](BASELINE_CONTAINER_DESIGN.md) | Original deep design spec for the baseline container. Some parts superseded by ARCHITECTURE.md — kept for provenance. |

## Grounded research

| Doc | Findings |
|---|---|
| [`SFN_FAN_OUT_RESEARCH.md`](SFN_FAN_OUT_RESEARCH.md) | Real AWS compute + workflow choices for the fan-out shape (Standard SFN, Parallel state, ECS Fargate via `ecs:runTask.sync`). |
| [`SFN_STATE_STRUCTURE_RESEARCH.md`](SFN_STATE_STRUCTURE_RESEARCH.md) | 5 public reference architectures examined — Prepare/Publish Lambdas rejected, containers-own-I/O pattern chosen. |

## Diagrams

Rendered PNGs live in [`diagrams/`](diagrams/). Sources: D2 for account topology, Mermaid for the two runtime flows.

- `accounts.png` — where each account sits + what lives in it
- `snapshot-analysis.png` — one-shot baseline compute
- `live-analysis.png` — recurring drift monitoring
