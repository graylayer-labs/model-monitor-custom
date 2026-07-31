# Configuration Contract (v2)

**Status:** Live (Phase 1 complete)  
**Related:** [`ARCHITECTURE.md`](../ARCHITECTURE.md) (system diagram)

---

## Overview

In the v2 system, **project configuration is a versioned S3 object**, not code or environment variables. This enables:

- **Declarative monitoring** — each monitor can be enabled/disabled, required/optional
- **Threshold updates without re-baselining** — thresholds live in config, not baselines
- **Audit trail** — S3 versioning tracks all config changes
- **Decoupling** — baseline and monitor stacks can be deployed independently of config changes

## Config Shape

One YAML block per project, versioned in the IaC repo. Deployed to S3 as JSON:

```yaml
projects:
  - name: project-a
    inference_account: "123456789012"
    producer_bucket_arn: "arn:aws:s3:::training-pipeline-bucket"
    
    endpoints:
      - name: project-a-prod
        schedule: "cron(0 * * * ? *)"           # hourly
        shadow_variant: "AllTraffic:shadow"     # null = disabled
    
    monitors:
      model_quality:
        enabled: true
        required: true                           # hard-fail baseline if artifacts missing
        ground_truth:
          lookback: "7d"                         # how far back to search for late labels
          min_coverage: 0.30                     # coverage floor (INSUFFICIENT_DATA if below)
        thresholds:
          f1_min: 0.85
      
      data_quality:
        enabled: true
        required: false                          # warn if artifacts missing, continue
        thresholds:
          drift_psi_max: 0.2
      
      bias:
        enabled: false                           # this monitor does not run
      
      explainability:
        enabled: false
      
      shadow:
        enabled: true
        required: false
        # no thresholds or ground_truth (live-vs-live comparison)
```

## S3 Layout

```
s3://config-<env>-<account>-<region>/
├── project-a/
│   ├── v1/config.json      # initial config (from commit sha abc123)
│   ├── v2/config.json      # threshold update (from commit sha def456)
│   └── v3/config.json      # new shadow endpoint (from commit sha ghi789)
└── project-b/
    ├── v1/config.json
    └── v2/config.json
```

**Versioning strategy:**
- Each project config has an independent version counter
- Versioning is **manual** in the IaC repo (developer assigns `v1`, `v2`, etc.)
- S3 versioning provides additional audit trail (exact timestamp of deploy)

## Semantics

### `enabled` vs `required`

- **`enabled: true`** — This monitor runs at all.
- **`enabled: false`** — This monitor is disabled; never runs.
- **`required: true`** — If artifacts are missing, baseline run **fails**. Pipeline bug = incident.
- **`required: false`** — If artifacts are missing, baseline run **warns** and skips this monitor. Data issue = expected.

These are independent: `enabled=false` overrides `required` (if a monitor is disabled, it doesn't matter if it's required).

### Ground Truth Join (Model Quality only)

- **`lookback: "7d"`** — How far back (from the monitor run date) to search for late labels.
- **`min_coverage: 0.30`** — Minimum join coverage (0.0–1.0). If matched labels / captured inferences < this, outcome is `INSUFFICIENT_DATA` (neither pass nor fail).

If labels stop arriving and coverage drops below this floor, the model's MQ score is invalidated (reported as data-flow health issue, not model health).

### Thresholds

- **Format:** dict of `threshold_name: numeric_value`
- **Per-monitor:** each monitor type (mq, dq, bias, explain) has its own threshold names
- **Updating thresholds:** change `config.json`, redeploy stacks, re-run monitoring. No re-baselining needed.

## ConfigStack

CDK stack `ConfigStack` creates and manages the config bucket:

- **Bucket name:** `mmc-config-<env>-<account>-<region>` (deterministic)
- **Encryption:** KMS (rotated annually)
- **Versioning:** S3 versioning enabled (audit trail of all puts)
- **Public access:** blocked
- **Lifecycle:** non-current versions expire after 90 days

## ConfigLoader

Python utility `ConfigLoader` reads `projects.yaml`, validates against schema, serializes to JSON for S3:

```python
from model_monitor_cdk.config_loader import ConfigLoader

loader = ConfigLoader(Path("environments/projects.yaml"))

# Serialize all projects
all_json = loader.to_json()

# Extract one project
project_json = loader.to_json_for_project("project-a")

# Generate S3 key
key = loader.s3_key_for_project("project-a", version=1)
# → "project-a/v1/config.json"
```

## Runtime Consumption

At runtime, stacks read config from `CONFIG_URI` environment variable (set by Baseline SFN's ResolveContext step):

```python
CONFIG_URI = "s3://config-prod-123456789012-eu-west-1/project-a/v3/config.json"

# Baseline SFN LoadAndGate step:
# 1. Fetch config from S3
# 2. Fetch manifest from S3
# 3. Compare: config.monitors vs manifest.artifacts
# 4. Decide which analysers run, which warn, which fail

# Monitor SFN ResolveContext step:
# 1. Fetch config from S3
# 2. Use thresholds + ground_truth config for analysis
```

## Non-goals (v1)

- Dynamic config reloads (stacks deployed with fixed config version)
- UI/dashboard for config updates (edit YAML, commit, redeploy)
- Config rollback automation (delete via console or git revert + redeploy)
