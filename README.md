# model-monitor-custom

Modern AWS batch analysis system. First use case: replace SageMaker Model Monitor + Clarify with own-container, own-math drift + bias + explainability monitoring. Decoupled by design — anything that writes to S3 can trigger a snapshot analysis run.

## Status

**Production-ready.** v2 Lambda-first compute backend complete and merged to main. Five analysers active (bias, dq, explain, mq, shadow).
292 tests passing (255 CDK + 37 containers). LocalStack E2E harness ready—no AWS credentials required for local testing.
Lambda is default compute; ECS available as toggleable option.

## Installation

### Prerequisites

- **AWS CLI** (for real deployments)
- **Docker** (for LocalStack and container builds)
- **uv** (Python package manager) — [install](https://docs.astral.sh/uv/getting-started/installation/)
- **Node.js** (for CDK)
- **git**

### Quick setup

1. Clone the repository:
   ```bash
   git clone https://github.com/graylayer-labs/model-monitor-custom.git
   cd model-monitor-custom
   ```

2. Install Python dependencies:
   ```bash
   uv sync --group dev
   ```

### Try locally (no AWS credentials needed)

Run the LocalStack E2E baseline test:

```bash
# Start LocalStack
docker compose -f docker-compose.localstack.yml up -d

# Run E2E tests
export LOCALSTACK_TEST_ENABLED=1
uv run pytest tests/e2e/test_localstack_baseline.py -v
```

No AWS account or credentials needed—LocalStack simulates S3, Lambda, DynamoDB, and CloudWatch locally.

### Deploy to real AWS

Deploying to your AWS accounts requires:

1. **Account setup**: Copy and configure account/project topology:
   ```bash
   cp cdk/environments/accounts.example.yaml cdk/environments/accounts.yaml
   cp cdk/environments/projects.example.yaml cdk/environments/projects.yaml
   $EDITOR cdk/environments/accounts.yaml cdk/environments/projects.yaml
   ```

2. **Bootstrap** (one-off per account/region):
   ```bash
   uv run cdk bootstrap --profile <your-profile> aws://<account-id>/eu-west-1
   ```

3. **Deploy**:
   ```bash
   uv run cdk deploy '*' --profile <your-profile>
   ```

See the [First deploy](#first-deploy) section below and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for detailed account topology and permissions setup.

## Architecture at a glance

Three subsystems coupled only by published JSON schemas.

```
[ Producer ]  →  s3://…/input/  →  [ Snapshot analysis ]  →  s3://…/output/  →  [ Live analysis ]  →  CW + DDB
```

Full mental model + I/O contract: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Design decisions (IaC layout, container base, anti-SageMaker guardrails): [`docs/design/`](docs/design/).
Non-negotiables (TDD, ruff, ty): [`docs/STANDARDS.md`](docs/STANDARDS.md).

### Account topology

![Accounts](docs/diagrams/accounts.png)

### Snapshot analysis (one-shot, per model version)

![Snapshot analysis](docs/diagrams/snapshot-analysis.png)

### Live analysis (recurring, per endpoint)

![Live analysis](docs/diagrams/live-analysis.png)

## Layout

```
cdk/           CDK v2 Python — constructs + stacks (Phase 2)
containers/    analyser containers (baseline, monitor)
shared/        published JSON schemas + optional Python helpers
docs/          design + architecture + standards + roadmap
  research/    grounded research backing the design decisions
  diagrams/    D2 + Mermaid sources + rendered PNGs
scripts/       local reproduce + parity helpers
tests/         unit + integration + fixtures
```

## Try it end-to-end

Runnable local example under [`examples/adult-classifier/`](examples/adult-classifier/) — trains a
small classifier on the UCI Adult fixture, drives every one of the five real analysers (bias,
explain, DQ, MQ, shadow) against the resulting splits, and regenerates
[`docs/e2e-output.md`](docs/e2e-output.md) with tables + plots. No AWS, no containers.

```
uv run python -m mmc_example_adult.run
```

![End-to-end DQ drift](examples/adult-classifier/outputs/plots/dq_drift_heatmap.png)

## Why this exists

SageMaker Model Monitor + Clarify have proven brittle: opaque errors, undocumented input shapes, 1/sec `CreateProcessingJob` throttle, upstream OSS repo dormant. Every production ML monitoring stack in public writing (Netflix, Uber, Airbnb, Evidently, WhyLabs, Arize, Fiddler) uses **custom containers on own compute**. This repo is that pattern, standalone.

Full evidence-backed case: [`docs/research/SM_MODEL_MONITOR_ASSESSMENT.md`](docs/research/SM_MODEL_MONITOR_ASSESSMENT.md).

## First deploy

Everything below assumes you have `aws`, `cdk`, `docker`, `uv`, and `git` on PATH,
and an AWS profile with credentials for the artifact account.

1. Copy the example configs and fill in real account IDs + producer bucket ARNs:

   ```
   cp cdk/environments/accounts.example.yaml cdk/environments/accounts.yaml
   cp cdk/environments/projects.example.yaml cdk/environments/projects.yaml
   $EDITOR cdk/environments/accounts.yaml cdk/environments/projects.yaml
   ```

2. Bootstrap every account the topology references (one-off per account/region):

   ```
   uv run cdk bootstrap --profile <artifact-profile> aws://<artifact-account>/eu-west-1
   uv run cdk bootstrap --profile <operations-profile> aws://<operations-account>/eu-west-1
   uv run cdk bootstrap --profile <inference-profile> aws://<inference-account>/eu-west-1
   ```

3. Deploy the artifact stack first (creates the ECR repos + baselines bucket + KMS key):

   ```
   uv run cdk deploy MMC-Test-Artifact --profile <artifact-profile>
   ```

4. Build + push the analyser container images to ECR:

   ```
   ./scripts/build-and-push-analysers.sh <artifact-account-id>
   ```

5. Deploy everything the topology declares. CDK filters stacks by account, so
   `'*'` works from each profile:

   ```
   uv run cdk deploy '*' --profile <artifact-profile>
   uv run cdk deploy '*' --profile <operations-profile>
   uv run cdk deploy '*' --profile <inference-profile>
   ```

## Contributing

R&D repo. Draft PRs only. TDD required — RED test before production code. Full contributor checklist in [`docs/STANDARDS.md`](docs/STANDARDS.md).
