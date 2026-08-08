# Documentation

Welcome! Start here to navigate the docs.

**New to the project?** Begin with the [root README](../README.md) for the overview + diagrams, then pick what you need from below.

**Want to contribute?** Start with [`STANDARDS.md`](#standards) to understand our code practices.

**Want to deploy to AWS?** Jump to [`CONFIGURATION.md`](#configuration) after reading [`ARCHITECTURE.md`](#architecture).

---

## Getting started

| Document | Purpose | Read time |
|----------|---------|-----------|
| [`../README.md`](../README.md) | Project overview, quick start, try-it-now section | 10 min |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | How to contribute: TDD workflow, testing, code style | 10 min |
| [`STANDARDS.md`](#standards) | Code standards we enforce (TDD, quality, commit messages) | 5 min |

## Core concepts

| Document | Purpose | Read time |
|----------|---------|-----------|
| [`ARCHITECTURE.md`](#architecture) | Mental model, system design, data contracts, subsystem boundaries | 20 min |
| [`CONFIGURATION.md`](#configuration) | Account topology, environment setup, how to customize deployments | 15 min |
| [`LOCALSTACK_TESTING.md`](#localstack) | Run the full system locally without AWS credentials | 10 min |
| [`ROADMAP.md`](#roadmap) | Project phases, current status, what's next | 5 min |

## Design decisions

Deep technical decisions are documented as ADRs (Architecture Decision Records) in [`design/`](design/):

| ADR | Decision | Status |
|-----|----------|--------|
| [`001-iac-layout.md`](design/001-iac-layout.md) | Monorepo with CDK, containers, shared schemas | ✅ Approved |
| [`002-container-base.md`](design/002-container-base.md) | Python 3.12 + Lambda Runtime Interface Client base image | ✅ Approved |
| [`003-anti-sagemaker-guardrails.md`](design/003-anti-sagemaker-guardrails.md) | Why we don't use SageMaker patterns | ✅ Approved |
| [`004-config-contract-v2.md`](design/004-config-contract-v2.md) | YAML-driven multi-account topology | ✅ Approved |

[Browse all design decisions →](design/)

## Research & evidence

Background research supporting the decisions above. Read the ADRs first, then dive into research if you want the full story.

| Research | Finding |
|----------|---------|
| [`research/SM_MODEL_MONITOR_ASSESSMENT.md`](research/SM_MODEL_MONITOR_ASSESSMENT.md) | Why SageMaker Model Monitor is problematic (evidence-backed) |
| [`research/IAC_DESIGN_RESEARCH.md`](research/IAC_DESIGN_RESEARCH.md) | Multi-account CDK reference architectures |
| [`research/SFN_FAN_OUT_RESEARCH.md`](research/SFN_FAN_OUT_RESEARCH.md) | Compute + workflow choices (Lambda vs ECS, SFN patterns) |
| [`research/SFN_STATE_STRUCTURE_RESEARCH.md`](research/SFN_STATE_STRUCTURE_RESEARCH.md) | 5 public SFN reference architectures evaluated |
| [`research/BASELINE_CONTAINER_DESIGN.md`](research/BASELINE_CONTAINER_DESIGN.md) | Deep design spec for baseline workflow (historical reference) |

[Browse all research →](research/)

## Visuals

Diagrams are maintained in `diagrams/` as both source and rendered PNGs:

| Diagram | Shows | Format |
|---------|-------|--------|
| [`diagrams/accounts.png`](diagrams/accounts.png) | AWS account topology (ml-artifact / ml-operations / ml-inference-*) | D2 |
| [`diagrams/snapshot-analysis.png`](diagrams/snapshot-analysis.png) | One-shot baseline compute flow (S3 → SFN → Lambda → S3) | Mermaid |
| [`diagrams/live-analysis.png`](diagrams/live-analysis.png) | Recurring drift monitoring flow (cron → SFN → Lambda → CW + DDB) | Mermaid |

[Browse all diagrams →](diagrams/)

## Reference docs

### STANDARDS.md {#standards}

Code quality, TDD, and testing standards we enforce.

**Read this when:**
- Contributing code
- Adding tests
- Refactoring

**Key topics:**
- TDD workflow (RED → GREEN → REFACTOR)
- Ruff linting + Pyright type checking
- Commit message format
- Diagram sync requirements

[Read `STANDARDS.md` →](STANDARDS.md)

### ARCHITECTURE.md {#architecture}

The mental model: what the system does, how subsystems interact, data contracts.

**Read this when:**
- Understanding how the system works
- Adding a new feature
- Designing a new stack
- Debugging integration issues

**Key topics:**
- System architecture (Producer → Snapshot → Live → Monitoring)
- Three subsystems (Config, Baseline, Monitor)
- Data contracts (JSON schemas)
- S3 layout convention
- Account topology
- State machine structures (ASL)

[Read `ARCHITECTURE.md` →](ARCHITECTURE.md)

### CONFIGURATION.md {#configuration}

How to configure and deploy the system to AWS.

**Read this when:**
- Deploying to your AWS account
- Adding a new project
- Configuring account topology
- Managing environments

**Key topics:**
- `accounts.yaml` schema
- `projects.yaml` schema
- Environment variables
- CDK deployment
- Multi-account setup

[Read `CONFIGURATION.md` →](CONFIGURATION.md)

### LOCALSTACK_TESTING.md {#localstack}

How to run the full system locally using LocalStack.

**Read this when:**
- Running tests without AWS
- Debugging test failures
- Setting up CI/CD
- Contributing changes

**Key topics:**
- Quick start
- Test architecture
- Troubleshooting common issues
- Performance notes
- Development workflow

[Read `LOCALSTACK_TESTING.md` →](LOCALSTACK_TESTING.md)

### ROADMAP.md {#roadmap}

Project phases, current status, and what's planned next.

**Read this when:**
- Understanding project status
- Planning contributions
- Checking blocking issues

**Key topics:**
- Phase 1 (current): Core system, LocalStack testing
- Phase 2 (next): Production hardening, real AWS validation
- Phase 3 (future): Expansion (new analysers, integrations)

[Read `ROADMAP.md` →](ROADMAP.md)

---

## FAQ

**Q: Where do I start if I'm new?**
1. Read the [root README](../README.md) (10 min)
2. Run the quick start: `python3 scripts/localstack-test-runner.py`
3. Read [`ARCHITECTURE.md`](#architecture) to understand the system
4. Pick an area to explore from above

**Q: I want to contribute. Where do I start?**
1. Read [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
2. Read [`STANDARDS.md`](#standards)
3. Pick an issue marked `good first issue`
4. Write a failing test first (TDD)
5. Open a draft PR for feedback

**Q: I'm deploying to AWS. What do I need to read?**
1. Read [`ARCHITECTURE.md`](#architecture) for the mental model
2. Read [`CONFIGURATION.md`](#configuration) for setup
3. Follow the "First deploy" section in the [root README](../README.md)

**Q: I'm debugging a test failure. What do I read?**
1. Run with verbose: `python3 scripts/localstack-test-runner.py --verbose`
2. Check [`LOCALSTACK_TESTING.md`](#localstack) → Troubleshooting section
3. Check the failing test's docstring for what it's testing

**Q: Where are the code examples?**
- [`examples/adult-classifier/`](../examples/adult-classifier/) — Runnable end-to-end demo
- Individual tests in `cdk/tests/` and `containers/*/tests/`

---

**Questions?** Open an issue or check the [Contributing guide](../CONTRIBUTING.md).
