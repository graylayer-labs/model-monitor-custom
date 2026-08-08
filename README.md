# model-monitor-custom

**Production-grade ML monitoring system for AWS.** Replace SageMaker Model Monitor + Clarify with your own containers, your own math, and full control. Drift, bias, explainability, data quality—all testable locally, deployable to any AWS account.

[![Tests](https://img.shields.io/badge/tests-292%20passing-brightgreen)](docs/STANDARDS.md)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![AWS](https://img.shields.io/badge/aws-lambda%2C%20stepfunctions%2C%20ddb-orange)](https://aws.amazon.com/)
[![License](https://img.shields.io/badge/license-MIT-gray)](#)

## What it does

```
Your ML Model → [ Drift Analysis ]  ──→ [ Explainability ]  ──→ [ Bias Detection ]
                       ↓                       ↓                      ↓
                   Data Quality          Feature Impact          Fairness Scores
                   Schema Check          SHAP Values             Per-class metrics
                   Completeness          Feature importance      Demographic parity
```

**Two workflows:**

1. **Snapshot Analysis** (one-shot per model version) — Validate a new baseline
   - Reads training data from S3
   - Runs 5 analysers in parallel (mq, dq, bias, explain, shadow)
   - Writes results to DynamoDB + S3
   - Approves/rejects for production

2. **Live Monitoring** (ongoing) — Watch inference traffic
   - Polls production predictions
   - Compares against approved baseline
   - Alerts on drift/bias
   - Logs to CloudWatch

## Try it right now (30 seconds, no AWS account needed)

```bash
# 1. Clone
git clone https://github.com/graylayer-labs/model-monitor-custom.git
cd model-monitor-custom

# 2. Install
uv sync --group dev

# 3. Run locally
python3 scripts/localstack-test-runner.py
```

**Output:**
```
[•] Starting LocalStack...
[✓] LocalStack is healthy
[•] Running pytest E2E tests...
tests/e2e/test_localstack_simple.py::test_baseline_registry_operations PASSED
tests/e2e/test_localstack_simple.py::test_s3_operations PASSED
tests/e2e/test_localstack_simple.py::test_baseline_workflow PASSED
tests/e2e/test_localstack_simple.py::test_lambda_invocation PASSED
[✓] All tests PASSED (4/4, ~34s)
```

No Docker setup, no AWS credentials, no manual infrastructure. Everything is automated.

## Why this exists

SageMaker Model Monitor is brittle:
- Opaque errors, undocumented input shapes
- 1/sec `CreateProcessingJob` throttle
- Upstream OSS repo dormant for 2+ years
- Forces you into their container shape (bad for custom metrics)

Every production ML team (Netflix, Uber, Airbnb, Evidently, WhyLabs, Arize, Fiddler) builds their own monitoring. This is that pattern, standalone and reproducible.

[Read the full assessment →](docs/research/SM_MODEL_MONITOR_ASSESSMENT.md)

## Architecture

Three decoupled subsystems, JSON-schema validated:

```
┌─────────────┐     S3 input      ┌──────────────────┐     S3 output      ┌────────────┐
│  Producer   │─────manifest.json─→ Snapshot Analysis │──results.json────→ Live Monitor │
└─────────────┘     training data  └──────────────────┘   outcomes table   └────────────┘
                                            ↓
                                    5 analysers in parallel:
                                    • Model Quality (schema)
                                    • Data Quality (completeness, drift)
                                    • Bias (demographic parity, fairness)
                                    • Explainability (SHAP, feature importance)
                                    • Shadow Mode (prediction disagreement)
```

### Systems at a glance

| System | Trigger | Compute | Output |
|--------|---------|---------|--------|
| **Snapshot** | New model version | Lambda × 5 | DynamoDB registry + S3 artifacts |
| **Live** | Hourly schedule | Lambda × 5 | CloudWatch metrics + alerts |
| **Config** | CD/CD | Lambda | YAML → DynamoDB lookup table |

[Full architecture & I/O contract →](docs/ARCHITECTURE.md)

## Key features

✅ **Locally testable** — Run full stack on your laptop with LocalStack (no AWS account)
✅ **Serverless** — Lambda + Step Functions (no servers to manage)
✅ **Configurable** — YAML-driven topology (multi-account, per-project settings)
✅ **Observable** — CloudWatch metrics, DynamoDB audit trail
✅ **Fast** — Parallel analysers, 5-10min for snapshot analysis
✅ **Safe** — KMS encryption, IAM roles, audit logging
✅ **Testable** — 292 tests (255 CDK + 37 container tests)

## Getting started

### Prerequisites
- **Docker** — for LocalStack and container builds
- **Python 3.11+** with **uv** — [install uv](https://docs.astral.sh/uv/getting-started/installation/)
- **Git**

For AWS deployment, also install:
- **AWS CLI** — `pip install awscli`
- **Node.js** 18+ — for CDK

### Try the example (no AWS)

Run the Adult Classifier example locally:

```bash
uv run python -m mmc_example_adult.run
```

This trains a model and runs all 5 analysers against it. Output: plots + summary.

![End-to-end DQ drift](examples/adult-classifier/outputs/plots/dq_drift_heatmap.png)

### Test your changes locally

After you modify code:

```bash
python3 scripts/localstack-test-runner.py --verbose
```

Tests run in LocalStack with real S3, DynamoDB, Lambda, and Step Functions. Exit code: 0 = pass, non-zero = fail.

[LocalStack testing guide →](docs/LOCALSTACK_TESTING.md)

### Deploy to AWS

1. **Configure your accounts:**
   ```bash
   cp cdk/environments/{accounts,projects}.example.yaml cdk/environments/
   $EDITOR cdk/environments/{accounts,projects}.yaml
   ```

2. **Bootstrap CDK (one-time per account):**
   ```bash
   uv run cdk bootstrap --profile <your-profile> aws://<account>/eu-west-1
   ```

3. **Deploy:**
   ```bash
   uv run cdk deploy '*' --profile <your-profile>
   ```

[Full deployment guide →](docs/CONFIGURATION.md)

## Project structure

```
├── cdk/                    AWS CDK infrastructure-as-code (Python)
│   ├── stacks/            Baseline, Monitor, Artifact, Trigger stacks
│   └── tests/             292 CDK unit + integration tests
│
├── containers/            5 analyser container images
│   ├── base/              Shared runtime (Python 3.12, mmc-base package)
│   ├── mq/                Model Quality analyser
│   ├── dq/                Data Quality analyser
│   ├── bias/              Bias Detection analyser
│   ├── explain/           Explainability (SHAP) analyser
│   └── shadow/            Shadow Mode analyser
│
├── scripts/
│   ├── localstack-test-runner.py    Automated test harness
│   └── build-and-push-analysers.sh  Docker → ECR pipeline
│
├── tests/
│   ├── e2e/               End-to-end tests (LocalStack)
│   └── stacks/            CDK synth tests
│
├── docs/
│   ├── ARCHITECTURE.md    Full system design + I/O contracts
│   ├── CONFIGURATION.md   Topology & environment variables
│   ├── LOCALSTACK_TESTING.md  Local test harness guide
│   ├── STANDARDS.md       Code standards (TDD, ruff, pyright)
│   └── design/            Design decisions (6 ADRs)
│
└── examples/              Runnable demos
    └── adult-classifier/  Train + analyse on UCI Adult dataset
```

[Browse full documentation →](docs/)

## Status

| Component | Status | Notes |
|-----------|--------|-------|
| **Snapshot Analysis** | ✅ Complete | Lambda compute, Step Functions orchestration |
| **Live Monitoring** | ✅ Complete | EventBridge triggers, DynamoDB outcomes table |
| **LocalStack Testing** | ✅ Complete | 4 E2E tests, ~34s execution |
| **Multi-account Deploy** | ✅ Complete | Config-driven topology (accounts.yaml + projects.yaml) |
| **Analyser Library** | ✅ Complete | 5 analysers (mq, dq, bias, explain, shadow) |

**Test coverage:** 292 passing (255 CDK, 37 container)

## Contributing

This is an active R&D project. Contributions welcome!

**Before you start:** Read [`docs/STANDARDS.md`](docs/STANDARDS.md)
- TDD required (RED test before code)
- Ruff + Pyright for quality
- Conventional commits

**To contribute:**
1. Create a feature branch: `git checkout -b feat/your-feature`
2. Write a failing test first
3. Implement the feature
4. Run tests: `python3 scripts/localstack-test-runner.py`
5. Submit a PR (draft OK, self-review required)

[Contributor guide →](docs/STANDARDS.md)

## Resources

| Document | Purpose |
|----------|---------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Mental model, data contracts, subsystem design |
| [LOCALSTACK_TESTING.md](docs/LOCALSTACK_TESTING.md) | Running tests without AWS |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Account topology, environments, secrets |
| [STANDARDS.md](docs/STANDARDS.md) | Code standards, TDD, testing requirements |
| [ROADMAP.md](docs/ROADMAP.md) | What's next (Phase 2 work) |
| [research/](docs/research/) | Evidence-backed decisions (SageMaker assessment, etc.) |

## FAQ

**Q: Can I run this without AWS?**
Yes! Use LocalStack (`python3 scripts/localstack-test-runner.py`). Runs on your laptop, no credentials needed.

**Q: Does this work with my existing model serving stack?**
Probably. If you can write model predictions to S3 as Parquet, this works. No SageMaker or specific framework required.

**Q: What's the cost?**
For a live monitoring setup: ~$15-30/month (Lambda + DynamoDB + CloudWatch). Snapshot analysis is cheaper (on-demand).

**Q: Can I add my own analyser?**
Yes. Add a new container in `containers/my-analyser/`, implement the interface, wire it into CDK. Framework handles orchestration.

**Q: Is this production-ready?**
Yes. Used in production deployments. TDD, type-checked, CDK tested against real AWS patterns.

---

**Questions?** Open an issue or check the [full documentation](docs/).
