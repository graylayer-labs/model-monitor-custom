# Docs

Start at the [root README](../README.md) for the overview + diagrams. Deep detail lives here.

## Core

| Doc | When to read |
|---|---|
| [`STANDARDS.md`](STANDARDS.md) | Before writing a line of code. Non-negotiables (TDD, ruff, ty). |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Mental model, system boundaries, container I/O contract, S3 layout, account topology. |
| [`IAC_DESIGN.md`](IAC_DESIGN.md) | CDK stack layout, deploy tool, naming, tags, Phase 2 build order. |
| [`ROADMAP.md`](ROADMAP.md) | Phase status + current task list. |

## Research

Grounded background — the receipts behind the decisions above. Read the digests in Core first.

| Doc | Findings |
|---|---|
| [`research/SM_MODEL_MONITOR_ASSESSMENT.md`](research/SM_MODEL_MONITOR_ASSESSMENT.md) | Evidence-backed case for moving off SageMaker Model Monitor + Clarify. |
| [`research/IAC_DESIGN_RESEARCH.md`](research/IAC_DESIGN_RESEARCH.md) | Multi-account CDK reference architectures. Stacks, deploy tools, naming. Digest → `IAC_DESIGN.md`. |
| [`research/SFN_FAN_OUT_RESEARCH.md`](research/SFN_FAN_OUT_RESEARCH.md) | Compute + workflow choices. Verdict: ECS Fargate + SFN Standard + Parallel + `ecs:runTask.sync`. |
| [`research/SFN_STATE_STRUCTURE_RESEARCH.md`](research/SFN_STATE_STRUCTURE_RESEARCH.md) | 5 public reference architectures — Prepare/Publish Lambdas rejected, containers-own-I/O chosen. |
| [`research/BASELINE_CONTAINER_DESIGN.md`](research/BASELINE_CONTAINER_DESIGN.md) | Deep design spec for the baseline container. Parts superseded by `ARCHITECTURE.md`; kept for provenance. |

## Diagrams

Sources + rendered PNGs in [`diagrams/`](diagrams/). D2 for the account grid; Mermaid for both runtime flows.

| Diagram | What it shows |
|---|---|
| `accounts.png` | AWS account topology (`ml-artifact` / `ml-operations` / `ml-inference-*`). |
| `snapshot-analysis.png` | One-shot baseline compute — S3 → SFN → Fargate → S3. |
| `live-analysis.png` | Recurring drift monitoring — cron → SFN → Fargate → CW + DDB → Pipes fan-out. |

Every runtime-behaviour PR updates the affected diagram + its source (see `STANDARDS.md` → "Diagrams stay in sync"). Render commands in `STANDARDS.md`.
