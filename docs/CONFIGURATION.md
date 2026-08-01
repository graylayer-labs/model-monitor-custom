# Configuration Guide

This guide documents the schema for `cdk/environments/accounts.yaml` and `cdk/environments/projects.yaml`, which drive the MMC deployment topology. Configuration is validated using Pydantic models defined in [`cdk/src/model_monitor_cdk/config.py`](../cdk/src/model_monitor_cdk/config.py).

## Quick Start

1. Copy the example configurations:
   ```bash
   cp cdk/environments/accounts.example.yaml cdk/environments/accounts.yaml
   cp cdk/environments/projects.example.yaml cdk/environments/projects.yaml
   ```

2. Edit with your AWS account IDs and S3 bucket ARNs.

3. Validate (no separate validation step needed — CDK deploy will catch errors):
   ```bash
   uv run cdk synth -c accounts=cdk/environments/accounts.yaml -c projects=cdk/environments/projects.yaml
   ```

---

## Accounts Configuration (`accounts.yaml`)

Top-level structure defines the AWS account topology and deployment region. Every stack in the CDK app consults this to determine which account to deploy into and which IAM roles to use.

### Schema

| Field | Type | Required | Default | Purpose | Example |
|-------|------|----------|---------|---------|---------|
| `region` | string | yes | — | AWS region for all stacks (single-region prototype) | `"eu-west-1"` |
| `roles` | object | yes | — | Account IDs for each topology role | see below |
| `operations_vpc_id` | string | no | `null` | VPC ID for OperationsBaselineStack to adopt (single-account collapse); omit or leave null to create a new /16 VPC | `"vpc-0123456789abcdef0"` |
| `github_oidc` | object | no | `null` | GitHub Actions OIDC role config for CI/CD (if omitted, no OIDC role provisioned) | see below |

### Roles Configuration (`roles`)

| Field | Type | Required | Length | Purpose | Example |
|-------|------|----------|--------|---------|---------|
| `artifact` | string | yes | 12 digits | AWS account ID that owns the baselines S3 bucket, artifact KMS CMK, and SharedIamStack (baseline reader/writer roles). All other accounts assume roles here. | `"111111111111"` |
| `operations` | string | yes | 12 digits | AWS account ID that runs offline snapshot analysers and baseline jobs. Assumes the baseline writer role in the `artifact` account to publish snapshots. In single-account topologies, same ID as `artifact`. | `"111111111111"` |
| `inference` | array | yes | ≥1 entry | List of one or more 12-digit AWS account IDs that run live monitoring (one per inference silo). Each entry gets its own reader role in SharedIamStack, and each project picks which one it lives in. | `["111111111111"]` |

**Validation:**
- Each account ID must be exactly 12 digits (string, not integer).
- All inference entries must be unique.

### GitHub OIDC Configuration (`github_oidc`)

Optional. When present, a GitHub Actions OIDC role is provisioned in the artifact account for CI/CD workflows (e.g., `.github/workflows/build-and-push-analysers.yml`).

| Field | Type | Required | Default | Purpose | Example |
|-------|------|----------|---------|---------|---------|
| `github_repo` | string | yes | — | GitHub repo slug (`owner/repo`) allowed to assume the push role | `"graylayer-labs/model-monitor-custom"` |
| `ref_filter` | string | no | `"refs/heads/main"` | Git ref filter for the OIDC token's `sub` claim (restricts which branches can push) | `"refs/heads/main"` |
| `create_provider` | bool | no | `true` | If `true`, create the GitHub OIDC identity provider. Set to `false` if the account already has one from another stack. | `true` |

**Example:**
```yaml
github_oidc:
  github_repo: "graylayer-labs/model-monitor-custom"
  ref_filter: "refs/heads/main"
  create_provider: true
```

### Complete Accounts Example

```yaml
region: eu-west-1

roles:
  artifact: "111111111111"
  operations: "111111111111"
  inference:
    - "111111111111"

operations_vpc_id: null

github_oidc:
  github_repo: "graylayer-labs/model-monitor-custom"
  ref_filter: "refs/heads/main"
  create_provider: true
```

**Single-account collapse:** Use the same 12-digit account ID for `artifact`, `operations`, and all `inference` entries.

---

## Projects Configuration (`projects.yaml`)

Top-level structure lists one entry per monitored model/silo. Each project mints one `InferenceMonitorStack` in the specified inference account and triggers baseline jobs in the operations account.

### Schema

| Field | Type | Required | Default | Purpose | Example |
|-------|------|----------|---------|---------|---------|
| `projects` | array | yes | — | One or more project specifications | see below |

### Project Specification

Each entry in `projects` has the following structure:

| Field | Type | Required | Default | Purpose | Example |
|-------|------|----------|---------|---------|---------|
| `name` | string | yes | — | Project slug (used in stack IDs, CloudWatch metrics, and tags). Must be at least 1 character. | `"fraud-detector-v1"` |
| `inference_account` | string | yes | — | 12-digit AWS account ID where this project's InferenceMonitorStack runs. Must be one of the IDs listed in `accounts.roles.inference`. | `"222222222222"` |
| `producer_bucket_arn` | string | yes | — | ARN of the S3 bucket owned by the producer team where training snapshots are uploaded. Used by OperationsBaselineStack to read training data and EventBridge to trigger baseline jobs on object-created events. | `"arn:aws:s3:::my-training-data"` |
| `producer_account` | string | no | `null` | 12-digit AWS account ID that owns the producer bucket. `null` or omit → producer lives in the operations account (single-account collapse). When set and different from operations, cross-account event forwarding is provisioned per ADR 010. | `"333333333333"` |
| `schedule` | string | no | `"cron(0 * * * ? *)"` | EventBridge Scheduler cron expression for the inference tick. Default is hourly. Overridden per-endpoint when `endpoints` is present. | `"cron(0 * * * ? *)"` |
| `vpc_id` | string | no | `null` | VPC ID for InferenceMonitorStack to adopt (single-account collapse). `null` or omit → stack creates its own /16 VPC. | `"vpc-0123456789abcdef0"` |
| `compute_backend` | string | no | `"lambda"` | Compute backend for running analysers: `"lambda"` (default, fully LocalStack-testable) or `"ecs"` (legacy Fargate path, kept for real-AWS parity testing). | `"lambda"` |
| `endpoints` | array | no | `[]` | (v2 feature) List of SageMaker endpoints under monitoring, each with schedule and optional shadow variant. When present, overrides the project-level `schedule`. | see below |
| `monitors` | object | no | `{}` | (v2 feature) Dict of monitor configs (keys: `mq`, `dq`, `bias`, `explain`, `shadow`), with enabled/required gating and per-monitor thresholds. | see below |

**Validation:**
- `inference_account` must be exactly 12 digits and must appear in `accounts.roles.inference`.
- `producer_bucket_arn` must match `arn:aws:s3:::<bucket-name>` (valid S3 ARN format).
- `producer_account` (if set) must be exactly 12 digits or null.
- `compute_backend` must be `"lambda"` or `"ecs"`.

### Endpoint Configuration (`endpoints`)

When present, overrides the project-level `schedule`. Each endpoint is monitored independently.

| Field | Type | Required | Default | Purpose | Example |
|-------|------|----------|---------|---------|---------|
| `name` | string | yes | — | SageMaker endpoint name. Used in schedules and metrics. Must be at least 1 character. | `"fraud-detector-prod"` |
| `schedule` | string | no | `"cron(0 * * * ? *)"` | EventBridge Scheduler cron for this endpoint's monitoring tick (hourly default). | `"cron(0 * * * ? *)"` |
| `shadow_variant` | string | no | `null` | (Optional) Shadow-variant name for Shadow analyser. When `null` or omitted, Shadow is disabled for this endpoint. | `"shadow-v1"` |

**Example:**
```yaml
endpoints:
  - name: fraud-detector-prod
    schedule: "cron(0 * * * ? *)"
    shadow_variant: shadow-v1
  - name: fraud-detector-staging
    schedule: "cron(0 */2 * * ? *)"
    shadow_variant: null
```

### Monitor Configuration (`monitors`)

(v2 feature) Each key names a monitor type (`mq`, `dq`, `bias`, `explain`, `shadow`). Omit a key to leave it at default settings.

| Field | Type | Required | Default | Purpose | Example |
|-------|------|----------|---------|---------|---------|
| `enabled` | bool | no | `true` | Whether this monitor runs for this project. | `true` |
| `required` | bool | no | `false` | Whether missing artifacts hard-fail (true) or warn (false) at baseline. Only checked if `enabled=true`. | `false` |
| `ground_truth` | object | no | `null` | (Model Quality only) Ground-truth join configuration. | see below |
| `thresholds` | object | no | `{}` | Per-monitor threshold dict (schema varies by monitor type). | `{"drift_threshold": 0.1}` |

### Ground Truth Configuration (`ground_truth`)

(Model Quality only) Configures the ground-truth label join.

| Field | Type | Required | Default | Purpose | Example |
|-------|------|----------|---------|---------|---------|
| `lookback` | string | no | `"7d"` | How far back to search for late labels (e.g., `"7d"`, `"14d"`). | `"7d"` |
| `min_coverage` | float | no | `0.30` | Minimum join coverage (0.0–1.0) to avoid INSUFFICIENT_DATA status. | `0.30` |

**Validation:**
- `min_coverage` must be between 0.0 and 1.0 (inclusive).

**Example:**
```yaml
monitors:
  mq:
    enabled: true
    required: false
    ground_truth:
      lookback: "7d"
      min_coverage: 0.30
    thresholds:
      accuracy_threshold: 0.85
  dq:
    enabled: true
    thresholds:
      constraint_violations_threshold: 0.05
  bias:
    enabled: false
  explain:
    enabled: true
  shadow:
    enabled: true
    thresholds:
      prediction_divergence: 0.1
```

### Complete Projects Example

```yaml
projects:
  - name: fraud-detector
    inference_account: "222222222222"
    producer_bucket_arn: "arn:aws:s3:::my-training-data"
    producer_account: "333333333333"
    schedule: "cron(0 * * * ? *)"
    vpc_id: null
    compute_backend: lambda
    endpoints:
      - name: fraud-prod
        schedule: "cron(0 * * * ? *)"
        shadow_variant: shadow-v1
      - name: fraud-staging
        schedule: "cron(0 */2 * * ? *)"
        shadow_variant: null
    monitors:
      mq:
        enabled: true
        required: false
        ground_truth:
          lookback: "7d"
          min_coverage: 0.30
      dq:
        enabled: true
      bias:
        enabled: false
      explain:
        enabled: true
      shadow:
        enabled: true

  - name: churn-predictor
    inference_account: "222222222222"
    producer_bucket_arn: "arn:aws:s3:::ml-training-snapshots"
    producer_account: null  # Same as operations account
    schedule: "cron(0 */6 * * ? *)"  # Every 6 hours
    vpc_id: null
    compute_backend: lambda
    # Minimal config — endpoints and monitors inherit defaults
```

---

## Account Topology Patterns

### Single-Account Collapse

All stacks deploy into the same AWS account. Minimal configuration:

```yaml
# accounts.yaml
region: eu-west-1
roles:
  artifact: "111111111111"
  operations: "111111111111"
  inference:
    - "111111111111"

# projects.yaml
projects:
  - name: my-model
    inference_account: "111111111111"
    producer_bucket_arn: "arn:aws:s3:::my-bucket"
    producer_account: null  # Same account as operations
```

### Multi-Account Topology

Separate artifact, operations, and inference accounts. More complex but better isolation:

```yaml
# accounts.yaml
region: eu-west-1
roles:
  artifact: "111111111111"       # Artifact account
  operations: "222222222222"     # Operations account
  inference:
    - "333333333333"             # Inference silo 1
    - "444444444444"             # Inference silo 2

# projects.yaml
projects:
  - name: model-1
    inference_account: "333333333333"  # Deploy in silo 1
    producer_bucket_arn: "arn:aws:s3:::ml-training"
    producer_account: "555555555555"   # Training data in separate account

  - name: model-2
    inference_account: "444444444444"  # Deploy in silo 2
    producer_bucket_arn: "arn:aws:s3:::ml-training"
    producer_account: "555555555555"
```

---

## Validation Rules

The config loader enforces the following cross-file constraints:

1. **Inference account must be known:** Every `project.inference_account` must appear in `accounts.roles.inference`.
2. **Account IDs are 12-digit strings:** Never integers; always quoted in YAML.
3. **S3 ARN format:** `arn:aws:s3:::<bucket-name>` (no trailing slashes or object keys).
4. **Unique inference accounts:** No duplicate entries in `accounts.roles.inference`.
5. **Cron expressions:** EventBridge Scheduler format (e.g., `"cron(0 * * * ? *)"`).

### Validation in Action

```python
from pathlib import Path
from model_monitor_cdk.config import load_env

accounts_path = Path("cdk/environments/accounts.yaml")
projects_path = Path("cdk/environments/projects.yaml")

config = load_env(accounts_path, projects_path)
# Raises ValueError if any constraint is violated
```

---

## Schema Definitions

Full Pydantic models are defined in [`cdk/src/model_monitor_cdk/config.py`](../cdk/src/model_monitor_cdk/config.py):

- `RolesConfig` — Account IDs for artifact, operations, inference
- `GithubOidcConfig` — Optional GitHub OIDC role
- `AccountsConfig` — Top-level accounts.yaml shape
- `EndpointConfig` — One SageMaker endpoint
- `GroundTruthConfig` — Ground-truth join parameters
- `MonitorConfig` — One monitor (mq, dq, bias, explain, shadow)
- `ProjectSpec` — One project specification
- `ProjectsConfig` — Top-level projects.yaml shape
- `EnvConfig` — Bundled accounts + projects, cross-validated

---

## Common Questions

### Can I monitor multiple endpoints in one project?

Yes. Use the `endpoints` array:

```yaml
projects:
  - name: my-model
    inference_account: "222222222222"
    producer_bucket_arn: "arn:aws:s3:::my-bucket"
    endpoints:
      - name: endpoint-1
        schedule: "cron(0 * * * ? *)"
      - name: endpoint-2
        schedule: "cron(0 */2 * * ? *)"
```

### Can I disable certain monitors?

Yes. Set `enabled: false` in the monitor config:

```yaml
monitors:
  bias:
    enabled: false  # Bias monitoring skipped
  dq:
    enabled: true
```

### What if I want to use an existing VPC?

Provide the VPC ID:

```yaml
projects:
  - name: my-model
    inference_account: "222222222222"
    producer_bucket_arn: "arn:aws:s3:::my-bucket"
    vpc_id: "vpc-0123456789abcdef0"
```

### When do I need cross-account event forwarding?

Set `producer_account` when the training data bucket is owned by a different AWS account than the operations stack:

```yaml
projects:
  - name: my-model
    producer_account: "555555555555"  # Different account → ProducerEventsStack created
```

### Can I use ECS instead of Lambda?

Yes. Set `compute_backend: ecs`:

```yaml
projects:
  - name: my-model
    inference_account: "222222222222"
    producer_bucket_arn: "arn:aws:s3:::my-bucket"
    compute_backend: ecs  # Use Fargate instead of Lambda
```

---

## Deployment

Once your configuration is finalized, deploy via CDK:

```bash
uv run cdk deploy '*' \
  -c accounts=cdk/environments/accounts.yaml \
  -c projects=cdk/environments/projects.yaml
```

CDK will synthesize all stacks based on the topology and filter by account when deploying from each AWS profile.
