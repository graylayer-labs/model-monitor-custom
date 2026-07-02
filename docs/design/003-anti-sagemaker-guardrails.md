# 003 — Anti-SageMaker guardrails

**Status:** Accepted.
**Related:** [`002-container-base.md`](002-container-base.md), [`../research/SM_MODEL_MONITOR_ASSESSMENT.md`](../research/SM_MODEL_MONITOR_ASSESSMENT.md).

## Context

This project exists because SageMaker Model Monitor + Clarify are the wrong shape for our workload. But `ml-core/containers/monitor/` and `ml-iac/` have battle-tested analyser math worth porting.

**Porting a class quietly imports its shape.** A base image alone does not stop `SM_*` env-var reads, Clarify field assumptions, `/opt/ml/` filesystem paths, or `content_template` JMESPath quirks from creeping back in. We need explicit guardrails, codified in CI + the base image.

## Decision

Seven guardrails, enforced before Phase 3 (real porting) begins.

### 1. Startup ban-list in base entrypoint

`mmc_base/entrypoint.py` first action:

```python
BANNED = ("SM_", "SAGEMAKER_")
found = [k for k in os.environ if k.startswith(BANNED)]
if found:
    raise RuntimeError(f"SageMaker env vars detected: {found}. This is model-monitor-custom — reshape the caller.")
```

Kills accidental container reuse from SageMaker Processing Jobs immediately.

### 2. Import ban via `pyproject.toml`

`containers/base/pyproject.toml` **omits** `sagemaker`, `smclarify-*` (except the specific `smclarify` bias math library, which is standalone and fine). Analyser images inherit — any `import sagemaker` fails at build.

Allowed AWS clients (base explicit whitelist): `boto3.client('s3'|'dynamodb'|'cloudwatch'|'sts')`. Analysers use base helpers, not raw boto3.

### 3. CI grep-guard

`.github/workflows/anti-sagemaker.yml`:

```bash
FORBIDDEN='SM_MODEL_DIR|SM_CHANNEL|SM_OUTPUT|/opt/ml/|CreateProcessingJob|content_template|probability_attribute|MonitoringExecution|SageMaker Model Monitoring Execution Status Change'
if grep -rEn "$FORBIDDEN" containers/ cdk/ shared/ 2>/dev/null; then
  echo "FAIL: SageMaker-shaped code detected"; exit 1
fi
```

Runs on every PR. Zero tolerance in `containers/`, `cdk/`, `shared/`. Research docs exempt (`docs/research/` is where we discuss what we're avoiding).

### 4. Schema `extra: forbid`

`shared/snapshot-input-schema.json` + all Pydantic models use `extra="forbid"`. Any Clarify-shaped field (`content_template`, `probability_attribute`, `label_headers`, etc.) slipping into a config file fails validation at container startup.

### 5. Port-header comment

Every file ported from `ml-core` / `ml-iac` gets a header:

```python
"""Ported from urbanfoxai/ml-core@<sha>:<path> on <date>.

Removed on port:
- SageMaker execution role assumption
- Clarify content_template layer
- /opt/ml/ filesystem paths
"""
```

Grep-able audit trail. No port lands without one.

### 6. Decision log entry per port

Every ported module → one line in `~/.claude/decisions.md`:

```
YYYY-MM-DD · model-monitor-custom · ported <path> from ml-core@<sha> — kept: <math>, stripped: <sagemaker-shape>.
```

### 7. Diagram integrity

Every new construct / handler / analyser must appear in the affected diagram (`snapshot-analysis.mmd`, `live-analysis.mmd`, or `accounts.d2`) in the same PR. Enforced by `STANDARDS.md` → "Diagrams stay in sync". If it can't be drawn cleanly, it doesn't belong.

## Traps this closes (reference table)

| Trap | Old code shape | Guardrail |
|---|---|---|
| SageMaker env vars | `SM_CHANNEL_INPUT`, `SM_MODEL_DIR`, `SM_OUTPUT_DATA_DIR` | 1, 3 |
| Clarify-shaped config | `content_template`, `probability_attribute`, JMESPath | 3, 4 |
| Clarify filenames as contract | `analysis.json` / `constraints.json` / `statistics.json` at file boundary | Own `result.json` shape in `002` |
| 1/sec Processing Job throttle | Retry loops tuned around `CreateProcessingJob` limit | 3 (grep `CreateProcessingJob`) |
| `Completed`/`Failed` two-state | Reads SageMaker MM lifecycle event | 3 (grep `MonitoringExecution`) |
| Vendor EventBridge | `SageMaker Model Monitoring Execution Status Change` (fake event) | 3 (grep) |
| Opaque failures | Container exits non-zero, no context | Base sidecar (`002`) |
| Hardcoded MPG wiring | Baseline tied to SageMaker Pipeline + MPG version | Snapshot triggered by S3 write; anything can produce inputs |
| `/opt/ml/processing/{input,output}` | Clarify container filesystem convention | 3 (grep `/opt/ml/`) |
| SageMaker execution role | Container inherits SM-managed IAM | 5 (port-header calls it out); ECS Fargate task role is explicit |

## Consequences

- Porting is slower — every port requires reshape + header + decision-log entry.
- CI blocks PRs that regress. Not negotiable.
- Research docs stay free to reference SageMaker — grep guard exempts `docs/research/`.
- If a guardrail becomes wrong (e.g. we genuinely need a new AWS service that trips the grep), amend by explicit PR to this doc — never a silent bypass.
