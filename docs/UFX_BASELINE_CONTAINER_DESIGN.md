# `ufx-baseline` — Custom Baseline Compute Container Design

> Status: Draft design proposal. Author: research spawned by UFX Manager. Date: 2026-07-02.
> Companion doc: [`SM_MODEL_MONITOR_ASSESSMENT.md`](SM_MODEL_MONITOR_ASSESSMENT.md) — the honest assessment of why we are doing this.
> Sibling container this mirrors: [`src/monitor_containers/ufx_monitor/`](../../monitoring-custom-container-explore/src/monitor_containers/ufx_monitor) on `feature/monitor-container`.
> Target consumer: [`analyzer_baseline_construct.py`](../../monitoring-custom-container-explore/src/stacks/ufx_ml_monitoring_stack/constructs/analyzer_baseline_construct.py) — the Clarify branch is the thing we are replacing.

---

## 0. TL;DR

Replace the two SageMaker Clarify Processing Job branches (Bias + Explainability) inside `AnalyzerBaselineConstruct` with a new `UfxBaselineConstruct` that launches a self-owned image, `ufx-baseline`. Same SFN. Same EventBridge trigger. Same S3 output paths and `analysis.json` shape. Different image URI, different container args, different IAM policy. ~350-450 LOC of container Python. ~120 LOC of CDK. Removes every §3 pain point in the assessment doc and lands us on AWS's own 2026 recommended pattern.

Non-goals: rebuilding `ufx-monitor`, `MonitoringScheduleStack`, `UfxMlMonitoringStack`, GT pipes, dashboards, alarms. Non-goals list matches assessment §8.

---

## Part A — Library survey

Each entry: what it is, install size, license, Python compatibility, maintenance activity, production users, verdict.

### A.1 `smclarify` — AWS's own OSS bias-metrics library

- **What it does.** The open-source portion of AWS Clarify. Ships:
  - `smclarify.bias.metrics` — enumerated PRETRAINING_METRICS and POSTTRAINING_METRICS registries. Standard SageMaker Clarify formulas.
  - `smclarify.bias.report.bias_report(df, facet_column, label_column, stage_type, predicted_label_column=None, metrics=["all"], group_variable=None) -> list[dict]` — the same function the closed-source Clarify container calls internally.
  - `smclarify.bias.metrics.basic_stats` — `accuracy`, `PPL`, `PNL`, `recall`, `specificity`, `precision`, `rejection_rate`, `conditional_acceptance`, `conditional_rejection`, `f1_score`, `proportion`, `observed_label_distribution`, `confusion_matrix` (post-training).
  - Utility layer `smclarify.util` — data-shape helpers.
- **Pre-training metrics** (conventional Clarify names, mapped to standard formulas): `CI` (Class Imbalance), `DPL` (Difference in Positive Proportions in Labels), `KL` (KL divergence), `JS` (Jensen–Shannon), `LP` (L_p norm), `TVD` (Total Variation Distance), `KS` (Kolmogorov–Smirnov), `CDDL` (Conditional Demographic Disparity in Labels).
- **Post-training metrics**: `DPPL`, `DI` (Disparate Impact), `DCA`, `DCO`, `RD`, `DLR`, `AD`, `TE`, `FT`, `CDDPL`.
- **Install size.** Wheel ~90 KB. Dependencies: `pandas`, `numpy`, `scipy`, `pyfunctional`, `pyarrow`. Total transitive install ~140 MB uncompressed (dominated by pandas + pyarrow).
- **License.** Apache-2.0.
- **Python compatibility.** Declared support Python 3.8+; imports and runtime use nothing that would break under Python 3.12. Confirmed importable in the `ufx_monitor` container's 3.12-slim base.
- **Maintenance activity.** Last tagged release `0.5` (April 4, 2023). 4 open issues, 0 open PRs. 138 total commits. Low activity — but that is fine: the library is a fixed set of arithmetic formulas that don't rot. Every dependency (`pandas`, `numpy`, `scipy`) is under aggressive independent maintenance and `smclarify` slots on top of them.
- **Production users.** AWS Clarify itself (closed-source container) is the reference user. No known third-party public writeups since AWS's 2026-06-30 Clarify wind-down announcement pointed customers at "smclarify or reimplement the formulas yourself."
- **Dependency traps.** None found in a 30-min audit. `pyfunctional` and `pyarrow` are the two heaviest transitive deps; both are widely deployed and stable.
- **Verdict.** **Use it.** Ships the exact bias formulas Clarify runs, in ~5-10 LOC of caller code per metric. Zero re-implementation risk. If AWS ever unpublishes it from PyPI (Part F.1 fallback), each formula is ~10 LOC to hand-roll.
- URLs: [aws/amazon-sagemaker-clarify](https://github.com/aws/amazon-sagemaker-clarify) · [`bias/report.py` source](https://github.com/aws/amazon-sagemaker-clarify/blob/master/src/smclarify/bias/report.py)

### A.2 `shap` — feature attribution

- **What it does.** Shapley-value-based feature-importance library. Same engine SageMaker Clarify uses internally per the [Clarify SHAP values doc](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-shapley-values.html).
- **Explainer classes.**
  - `TreeExplainer` — exact fast algorithm for tree ensembles (XGBoost, LightGBM, CatBoost, scikit-learn, PySpark). Milliseconds per row. **Not applicable** to our BiGRU sequence model.
  - `DeepExplainer` — designed for TensorFlow/Keras. "Preliminary PyTorch support" per docs — the PyTorch path has been known-buggy for RNNs specifically, wraps around `torch.autograd`. **Risk-heavy** for a first shipping version.
  - `GradientExplainer` — expected-gradients approach for TF/Keras/PyTorch. Works for PyTorch. Not standard in Clarify — no downstream shape compatibility.
  - `KernelExplainer` — model-agnostic. Takes any callable `model(X) -> [n, n_classes]`. Slower (O(nsamples · background)), but the safe choice for our model class. **This is what Clarify uses internally when the model is not a native tree/linear.**
- **Recommendation for BOS_SEQ (BiGRU + attention, multiclass 3-way).** `KernelExplainer` with a `predict_proba`-shaped wrapper around the loaded PyTorch model. `shap.kmeans(background, K=50)` to summarize the background set (see A.2 background sizing below). Nsamples = 100 initially; expand as runtime allows.
- **Install size.** Source dist `shap-0.52.0.tar.gz` ~4.2 MB. Wheels 490 KB (manylinux) – 1.6 MB (musllinux). Runtime deps: `numpy`, `scipy`, `scikit-learn`, `pandas`, `tqdm`, `packaging`, `slicer`, `cloudpickle`, `numba`. **`numba` is the heavy one** — ~90 MB installed, JIT-compiles at import time (~2s cold-start), pulls `llvmlite` (~50 MB more).
- **License.** MIT.
- **Python compatibility.** Latest `shap==0.52.0` requires Python 3.12+. Perfect for us.
- **Maintenance activity.** Latest release May 2026. Actively developed, dozens of contributors, ~4k open issues (mostly usage-questions, healthy signal).
- **Production users.** Widely deployed — most of the ML-observability vendor stack (Arize, Fiddler, WhyLabs) and the AWS-samples 2026 monitoring reference all depend on `shap`.
- **Verdict.** **Use `shap.KernelExplainer`.** Slow but correct and model-agnostic. Well-understood shape. Same output structure Clarify writes.
- URLs: [shap on PyPI](https://pypi.org/project/shap/) · [`KernelExplainer` API](https://shap.readthedocs.io/en/latest/generated/shap.KernelExplainer.html)

### A.3 `evidently` — full drift + bias + reporting

- **What it does.** Full ML-monitoring library: 100+ metrics across drift, data quality, classification, regression, LLM/text, RAG.
- **Overlap with our need.** Data-drift and data-quality overlap heavily with what `ufx-monitor`'s DQ analyzer already computes; bias overlap is partial (evidently emits `Bias` under classification metrics but not the full SageMaker Clarify metric set). No feature-attribution / SHAP support — Evidently docs explicitly recommend pairing with `shap` for that.
- **Install size.** ~40 MB wheel + pandas + scipy + plotly transitive. Total ~250 MB installed.
- **License.** Apache-2.0.
- **Python compatibility.** 3.9+ per current pyproject.
- **Maintenance activity.** 7.7k stars, 2,795 commits, latest release `v0.7.21` (March 2026), 872 forks. Very active.
- **Verdict.** **Don't adopt for baseline compute.** Evidently is a scheduled-drift comparison library (reference vs. current), which is what `ufx-monitor` already does. Baseline compute is one-shot statistic emission — a smaller, simpler surface. Keeping it out avoids: (a) an extra 250 MB in every image, (b) a second config surface (evidently `Report` schema is its own DSL), and (c) coupling the baseline shape to evidently's output. Revisit for the `ufx-monitor` side of the house if we ever want to replace our hand-rolled DQ analyzer.
- URL: [evidentlyai/evidently](https://github.com/evidentlyai/evidently)

### A.4 `whylogs` — streaming profiles

- **What it does.** Data logging library. Emits compact, mergeable statistical profiles (streaming sketches — HLL, KLL, count-min, etc.) rather than full statistics.
- **Where it would fit.** Excellent for the inference-time-capture side (`ufx-monitor`'s live analyzer), where per-row logging cost matters. **Poor fit** for baseline compute — baseline is a one-shot batch, no need for mergeable sketches.
- **Install size.** ~15 MB wheel + protobuf + datasketches transitive. Total ~80 MB installed.
- **License.** Apache-2.0.
- **Maintenance.** 2.8k stars, 936 commits, latest v1.6.4 (Dec 2024), 173 releases. Active.
- **Verdict.** **Don't adopt for baseline.** Different design goal — streaming profiles, not batch-shot bias/attribution.
- URL: [whylabs/whylogs](https://github.com/whylabs/whylogs)

### A.5 One-liners on other contenders

| Library | One-line take |
|---|---|
| `deepchecks` | Full suite of ML checks. Good docs. Overlaps evidently — same "don't need it here" logic. |
| `alibi` | Excellent for counterfactual + local explanations. Overkill for our need — we ship a single per-class feature importance vector. |
| `alibi-detect` | Drift-detection cousin of `alibi`. Same "solves the monitor problem, not the baseline problem" story as evidently. |
| `fairlearn` | Microsoft's fairness library. Non-SageMaker Clarify formulas — different metric-name space. Would break `analysis.json` shape compat. Skip. |
| `captum` | PyTorch-native attribution (integrated gradients, layer conductance). Better than SHAP for RNNs *if* we accept a per-model wrapper. Deferred to Part F.2 for reconsideration in a follow-up if `KernelExplainer` runtime bites. |

**Chosen library set for `ufx-baseline`:**

```
smclarify         # bias metrics (Clarify-compatible output)
shap              # KernelExplainer for BiGRU
torch             # model load
scikit-learn      # shap.kmeans background summarisation, ColumnTransformer for feature encoding
pandas / numpy    # data prep — smclarify takes DataFrames
boto3             # S3 + STS
pydantic          # config parsing (matches ufx_monitor)
```

Everything else declared in `analysis_config.json` today (JMESPath fields, `content_template`, etc.) is deleted from ml-core once the cutover completes — those exist only to satisfy the closed-source Clarify container.

---

## Part B — Container design

### B.1 File layout under `src/monitor_containers/ufx_baseline/`

Mirror `ufx_monitor/` shape. One-to-one where possible so anyone who reads `ufx_monitor` can navigate `ufx_baseline`.

```
src/monitor_containers/ufx_baseline/
├── Dockerfile
├── entrypoint.sh
├── pyproject.toml
├── README.md
├── ufx_baseline/
│   ├── __init__.py
│   ├── cli.py                    # env-driven entrypoint, dispatches to analyzer
│   ├── schemas.py                # BaselineType enum, ContainerEnv, AnalysisConfig (pydantic)
│   ├── analyzers/
│   │   ├── __init__.py
│   │   ├── base.py               # BaselineAnalyzer protocol, BaselineResult dataclass
│   │   ├── bias.py               # smclarify wrapper — pre/post-training bias
│   │   └── explainability.py     # shap.KernelExplainer wrapper
│   ├── io/
│   │   ├── __init__.py
│   │   ├── dataset.py            # read JSONL from /opt/ml/processing/input/data
│   │   ├── config.py             # read analysis_config.json from /opt/ml/processing/input/config
│   │   ├── model.py              # download + unpack model.tar.gz, materialise a predict_proba callable
│   │   └── outputs.py            # write analysis.json + failure.json + _provenance.json
│   └── model_adapters/
│       ├── __init__.py
│       ├── base.py               # ModelAdapter protocol — model + predict_proba(X)
│       └── bos_seq.py            # BiGRU-specific adapter reusing ml-core inference.model_fn
└── tests/
    ├── conftest.py
    ├── analyzers/
    │   ├── test_bias.py          # smclarify happy path + edge (empty facet, single-value label)
    │   └── test_explainability.py # shap wrapper against a mock predict_proba
    ├── io/
    │   ├── test_dataset.py
    │   ├── test_config.py
    │   └── test_model.py
    └── test_cli.py               # env-driven dispatch + failure.json sidecar path
```

Key differences from `ufx_monitor/`:
- No `filters/` — the input is already scoped to one baseline artefact.
- No `join/` — bias and explainability read a single dataset; there's no capture-vs-GT stream join.
- Extra `model_adapters/` — SHAP needs a live callable; per-project model-load stays isolated in one file per model. First adapter: `bos_seq`. Second (agg XGB): `bos_agg` — trivial, `TreeExplainer` path. See B.6.

### B.2 Dockerfile

Base image discussion:

- **`python:3.12-slim`** (what `ufx_monitor` uses). ~40 MB base, minimal surface. Chosen for parity — same base image means same `libgomp1` gotcha, same `uv` layer cache, same familiar package pinning.
- **SageMaker base image** (e.g. `763104351884.dkr.ecr.<region>.amazonaws.com/pytorch-training:2.x`). ~5 GB. Ships PyTorch + CUDA + Miniconda pre-baked. **Rejected** — CUDA is useless for CPU-only `KernelExplainer`, and the size makes CI push slow.
- **`debian:12-slim`** without Python. Would need to install Python 3.12 ourselves. No benefit over `python:3.12-slim`.

**Chosen: `python:3.12-slim`.**

Additional apt packages: `libgomp1` (already needed by `ufx_monitor` for lightgbm — `smclarify` transitive doesn't need it but `shap` uses `numba` which links against `libgomp`; keeping it in avoids a "why is CI red" moment). Everything else in the deps list is pure-Python or ships manylinux wheels.

Target image size: **≤ 1.2 GB compressed**. Torch alone is ~800 MB; if that bites we can move to `torch==2.x --index-url https://download.pytorch.org/whl/cpu` which is ~200 MB — recommended in Part E for cost.

```dockerfile
# src/monitor_containers/ufx_baseline/Dockerfile
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:0.5.18 /uv /usr/local/bin/uv

# libgomp1: numba (shap dep) links against OpenMP runtime.
# libstdc++6 is already present in the slim base.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- deps layer (cached across code changes) ---
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# --- code layer ---
COPY ufx_baseline ./ufx_baseline
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV PATH="/opt/venv/bin:$PATH"

ENTRYPOINT ["/entrypoint.sh"]
```

Rationale for layer order: `pyproject.toml + uv.lock` change rarely, dep resolve is ~90s; source changes constantly, ~5s. Puts the expensive layer first so incremental CI builds are fast.

`entrypoint.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
exec python -m ufx_baseline.cli "$@"
```

### B.3 Entrypoint contract

We keep the **SageMaker Processing Job contract** — the `AnalyzerBaselineConstruct` already emits `CreateProcessingJob` and the mount points `/opt/ml/processing/{input/data, input/config, output}` are stable. Reusing that contract avoids touching the SFN topology.

Env vars consumed by `cli.py`:

| Env var | Required | Purpose |
|---|---|---|
| `PACKAGE_GROUP_NAME` | yes | MPG name — emitted into `_provenance.json`, used in log preamble. |
| `BASELINE_VERSION` | yes | Integer version segment. Emitted into provenance. |
| `MONITOR_TYPE` | yes | `bias` \| `explainability`. Selects analyzer. |
| `MODEL_ADAPTER` | yes for `explainability` | Which `model_adapters/` module to load. E.g. `bos_seq`. Needed because different models unpack differently. |
| `MODEL_S3_URI` | yes for `explainability` | `s3://.../model.tar.gz` — downloaded, unpacked, adapter-loaded. Not needed for bias (bias reads labels + predictions from the dataset). |
| `INPUT_DATA_DIR` | optional | Default `/opt/ml/processing/input/data`. |
| `INPUT_CONFIG_DIR` | optional | Default `/opt/ml/processing/input/config`. |
| `OUTPUT_DIR` | optional | Default `/opt/ml/processing/output`. |
| `SHAP_NSAMPLES` | optional | Default `100`. |
| `SHAP_BACKGROUND_K` | optional | Default `50`. |
| `LOG_LEVEL` | optional | Default `INFO`. |

Notes:
- We **do not** pass `INPUT_S3_URI` / `OUTPUT_S3_URI` as env vars — SageMaker mounts those under the fixed local paths above, exactly as Clarify did today. Keeps the CDK construct simpler (no S3 URI templating).
- `CONFIG_S3_URI` also stays a mount, not an env var, for the same reason.
- `SAGEMAKER_ROLE_ARN` is **not** consumed by the container — the role is assumed by SageMaker as the Processing Job's execution role; the container reads its temporary credentials from the standard `AWS_*` env vars SageMaker injects.

### B.4 Config parsing — keep `analysis_config.json` shape or design cleaner?

Options:

**Option A — Keep Clarify's `analysis_config.json` shape verbatim.**
Pros: zero ml-core change; existing `emit_baseline_inputs.py` output for the Clarify path continues to work; the moment we cut over CDK-side, the Python code just reads a JSON we already emit.
Cons: we inherit the shape warts flagged in §3b of the assessment (`content_template` requiring literal `$features`, `label_headers` semantics, JMESPath in `features` field). Those warts exist to feed the closed-source container; they add zero value to us.

**Option B — Design a cleaner UFX-owned schema, adapt ml-core to emit it.**
Pros: only carry the fields we actually use — `label_column`, `facet_columns`, `predicted_label_column`, `positive_label_values`, `class_labels`, `shap` block with `nsamples` + `background_size` + `feature_columns`.
Cons: coordinated change ml-iac + ml-core; two config schemas in flight during Phase 2 parallel-run.

**Recommendation: Option B, with a transitional reader.**

```python
# ufx_baseline/io/config.py
from pydantic import BaseModel, Field

class ShapConfig(BaseModel):
    nsamples: int = 100
    background_size: int = 50
    feature_columns: list[str]
    class_labels: list[str] | None = None

class UfxBaselineConfig(BaseModel):
    """UFX-native baseline config. Emitted by ml-core's baseline emitter.

    Attributes:
        schema_version: Bumped when we change the shape (start at 1).
        label_column: Column name in the input JSONL carrying ground truth.
        predicted_label_column: Column name carrying model prediction. Absent for
            pre-training bias only.
        facet_columns: One or more protected-attribute columns; bias metrics loop over them.
        positive_label_values: Values in ``label_column`` treated as favourable outcome.
        problem_type: ``BinaryClassification`` | ``MulticlassClassification`` | ``Regression``.
        class_labels: Ordered class names (needed for multiclass metric naming). Optional
            for binary.
        shap: SHAP settings — omitted for bias-only jobs.
    """
    schema_version: int = 1
    label_column: str
    predicted_label_column: str | None = None
    facet_columns: list[str] = Field(default_factory=list)
    positive_label_values: list[str | int | float]
    problem_type: str
    class_labels: list[str] | None = None
    shap: ShapConfig | None = None
```

Transitional reader (during Phase 2 parallel-run):

```python
def load_config(config_dir: Path) -> UfxBaselineConfig:
    """Load the UFX baseline config, tolerating the transitional Clarify shape.

    Reads ``analysis_config.json`` from ``config_dir``. If the JSON contains a
    ``schema_version`` key, parse as :class:`UfxBaselineConfig`. Otherwise,
    project the Clarify shape onto the UFX shape (documented mapping table
    below) and log a deprecation warning.

    Args:
        config_dir: Directory containing ``analysis_config.json``.

    Returns:
        UfxBaselineConfig populated from either shape.

    Raises:
        FileNotFoundError: When ``analysis_config.json`` is missing.
        ValueError: On unrecognised shape.
    """
```

Mapping table for the transitional reader:

| Clarify field | UFX field |
|---|---|
| `label` | `label_column` |
| `predicted_label` | `predicted_label_column` |
| `facet[].name_or_index` | `facet_columns[]` |
| `label_values_or_threshold` | `positive_label_values` |
| `dataset_type` | (discarded) |
| `methods.pre_training_bias.methods` | (discarded — we always compute the full set) |
| `methods.shap.baseline` | `shap.background_size` (derived) |
| `methods.shap.num_samples` | `shap.nsamples` |
| `methods.shap.agg_method` | (discarded — SHAP defaults) |

### B.5 BIAS analyzer

`smclarify.bias.report.bias_report` takes a DataFrame + facet/label columns + stage type. Wrap it thinly.

```python
# ufx_baseline/analyzers/bias.py
from dataclasses import dataclass
import pandas as pd
from smclarify.bias.report import bias_report, FacetColumn, LabelColumn, StageType
from ufx_baseline.schemas import BaselineResult

@dataclass(frozen=True)
class BiasAnalyzer:
    """Compute pre-training and (when predictions present) post-training bias.

    Attributes:
        df: Input dataset — one row per session, must include label + facet cols.
        label_column: Column name for the ground-truth label.
        predicted_label_column: Column name for the model prediction. When
            None, only pre-training metrics run.
        facet_columns: One or more protected attribute column names. The report
            loops over each; the output analysis.json contains one bias block
            per facet.
        positive_label_values: Values in the label column treated as favourable.
    """

    df: pd.DataFrame
    label_column: str
    predicted_label_column: str | None
    facet_columns: list[str]
    positive_label_values: list

    def compute(self) -> BaselineResult:
        """Run smclarify bias_report per facet and stitch into one BaselineResult.

        Returns:
            BaselineResult with an ``analysis`` dict shaped compatibly with
            Clarify's analysis.json (see §B.5 output shape below).
        """
        analysis = {"version": "1.0", "pre_training_bias_metrics": {}, "post_training_bias_metrics": {}}
        for facet in self.facet_columns:
            facet_col = FacetColumn(name=facet)
            label_col = LabelColumn(
                name=self.label_column,
                data=self.df[self.label_column],
                positive_label_values=self.positive_label_values,
            )
            pre_rows = bias_report(
                df=self.df,
                facet_column=facet_col,
                label_column=label_col,
                stage_type=StageType.PRE_TRAINING,
                metrics=["all"],
            )
            analysis["pre_training_bias_metrics"][facet] = pre_rows
            if self.predicted_label_column:
                pred_col = LabelColumn(
                    name=self.predicted_label_column,
                    data=self.df[self.predicted_label_column],
                    positive_label_values=self.positive_label_values,
                )
                post_rows = bias_report(
                    df=self.df,
                    facet_column=facet_col,
                    label_column=label_col,
                    stage_type=StageType.POST_TRAINING,
                    predicted_label_column=pred_col,
                    metrics=["all"],
                )
                analysis["post_training_bias_metrics"][facet] = post_rows
        return BaselineResult(analysis=analysis)
```

**Multi-class positive-label sets.** `smclarify` accepts a list — `positive_label_values=["Bot"]` treats "Bot" as favourable and folds the other two classes into the disfavoured group. For UFX BOS_SEQ we want to run three bias reports — one per class — each with a single-element positive list. The wrapper loops:

```python
if problem_type == "MulticlassClassification":
    for cls in class_labels:
        # ... call bias_report with positive_label_values=[cls] and stash under
        # analysis["pre_training_bias_metrics"][facet][cls]
```

**Output shape.** We choose to mirror Clarify's `analysis.json` structure **at the top level** so `ufx_monitor/analyzers/bias.py:read_analysis` (already parses `analysis.json` from the current Clarify path) needs no change. Downstream compatibility → zero drift.

Reference: [`analysis.json` shape](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-config-json-analysis.html).

```json
{
  "version": "1.0",
  "pre_training_bias_metrics": {
    "actor_type": {
      "Bot":       [{"name": "CI",  "value": 0.12, "description": "..."}, ...],
      "Organic":   [...],
      "Synthetic": [...]
    }
  },
  "post_training_bias_metrics": {
    "actor_type": { ... same shape ... }
  },
  "explanations": {
    "kernel_shap": {
      "actor_type": {
        "global_shap_values": {
          "feature_a": 0.34,
          "feature_b": -0.02,
          ...
        },
        "expected_value": [0.33, 0.33, 0.34]
      }
    }
  }
}
```

### B.6 EXPLAINABILITY analyzer — SHAP wrapper

```python
# ufx_baseline/analyzers/explainability.py
from dataclasses import dataclass
from typing import Callable
import numpy as np
import pandas as pd
import shap

@dataclass(frozen=True)
class ExplainabilityAnalyzer:
    """Compute global SHAP feature importance via KernelExplainer.

    Attributes:
        df: Full dataset — background sampling drawn from this.
        feature_columns: Ordered feature names — column order matches the
            model adapter's expected input.
        predict_proba: Callable ``(X: np.ndarray[n, f]) -> np.ndarray[n, c]``
            producing per-class probabilities. Wired by the model adapter.
        class_labels: Names for the c output classes.
        nsamples: SHAP sample count per row. Default 100.
        background_size: Rows to summarise the background with (kmeans). Default 50.
        explain_size: How many dataset rows to compute local SHAP for. Global
            importance is the mean absolute of these local values. Default 200.
    """

    df: pd.DataFrame
    feature_columns: list[str]
    predict_proba: Callable[[np.ndarray], np.ndarray]
    class_labels: list[str]
    nsamples: int = 100
    background_size: int = 50
    explain_size: int = 200

    def compute(self) -> dict:
        """Return a dict shaped for analysis.json['explanations']['kernel_shap'].

        Runtime O(explain_size · nsamples · background_size · predict_proba_cost).

        Returns:
            A dict with per-class global SHAP feature importances.
        """
        X = self.df[self.feature_columns].to_numpy()
        background = shap.kmeans(X, self.background_size)
        explainer = shap.KernelExplainer(self.predict_proba, background, link="logit")
        sample_idx = np.random.default_rng(seed=0).choice(len(X), size=min(self.explain_size, len(X)), replace=False)
        sample = X[sample_idx]
        shap_values = explainer.shap_values(sample, nsamples=self.nsamples, silent=True)
        # shap_values: for multi-output — shape (n_samples, n_features, n_classes) since v0.45
        global_shap = {}
        for cls_idx, cls_name in enumerate(self.class_labels):
            per_feat_mean_abs = np.mean(np.abs(shap_values[..., cls_idx]), axis=0)
            global_shap[cls_name] = {
                feat: float(val) for feat, val in zip(self.feature_columns, per_feat_mean_abs, strict=True)
            }
        return {
            "global_shap_values": global_shap,
            "expected_value": explainer.expected_value.tolist()
            if hasattr(explainer.expected_value, "tolist") else explainer.expected_value,
            "config": {
                "nsamples": self.nsamples,
                "background_size": self.background_size,
                "explain_size": len(sample),
            },
        }
```

Deterministic seed on background sampling and explain-sample selection so re-runs of the same input produce identical `analysis.json` — makes the Phase 2 parallel-run comparison feasible.

### B.7 Model loading

The BiGRU inference handler at [`~/UFX/ML/ml-core/projects/bos_sess_seq_clf/src/inference.py`](../../../../ml-core/projects/bos_sess_seq_clf/src/inference.py) implements the four SageMaker handlers:

```
model_fn(model_dir)  -> ModelBundle(model, preprocessor, id_to_label, feature_cols)
input_fn(payload, content_type)
predict_fn(input_data, model_bundle)
output_fn(prediction, accept)
```

**Decision: reuse `model_fn` from ml-core.** Copying/re-implementing model-load logic in ml-iac creates the exact drift the SYSTEM `Known Gaps` warns about. Instead, publish ml-core's `bos_sess_seq_clf` package to a private ECR-adjacent artefact or vendor a minimal subset (the `model_fn` + supporting `models/model.py`).

Concrete approach — vendor via **git subtree or Poetry-style path dependency**:

- `ufx_baseline/model_adapters/bos_seq.py` — thin shim:
  ```python
  from bos_sess_seq_clf.inference import model_fn  # vendored path dep or pip
  from ufx_baseline.model_adapters.base import ModelAdapter
  import numpy as np, torch

  class BosSeqAdapter(ModelAdapter):
      def load(self, model_dir: Path) -> None:
          self.bundle = model_fn(str(model_dir))

      def predict_proba(self, X: np.ndarray) -> np.ndarray:
          """SHAP feeds flat [n, f]. We wrap into the model's expected shape.

          BOS_SEQ takes session-aggregate features (per this week's flatten fix
          from squad ml-core BOS_SEQ baseline flattener). No sequence reshape
          needed — each row is one session.
          """
          X_processed = self.bundle.preprocessor.transform(pd.DataFrame(X, columns=self.feature_cols))
          tensor = torch.as_tensor(X_processed, dtype=torch.float32)
          with torch.no_grad():
              # Model API: (features, attention_mask) → logits
              mask = torch.ones(tensor.shape[:2], dtype=torch.bool)
              logits = self.bundle.model(tensor.unsqueeze(1), mask)  # [n, 1, f] → [n, C]
              return torch.softmax(logits, dim=-1).numpy()
  ```

**Model download.** SageMaker Processing Job supports `ModelInput` — but keeping the download in-container (via boto3) gives us structured error surfacing (see B.8) and one less CDK moving part. Approx 15 LOC in `io/model.py`.

### B.8 Error handling

The whole point of this rewrite is that Clarify's failure mode was a single-line opaque error from a closed-source container. We do better.

- Every uncaught exception writes:
  1. Full stack trace to CloudWatch logs (default logging).
  2. Structured `failure.json` sidecar to `/opt/ml/processing/output/` — schema:
     ```json
     {
       "schema_version": 1,
       "monitor_type": "bias",
       "package_group_name": "bos-sess-seq-clf",
       "baseline_version": 3,
       "phase": "compute" | "config-load" | "dataset-load" | "model-load" | "output-write",
       "error_class": "ValueError",
       "message": "Facet column 'actor_type' not present in dataset",
       "traceback": ["...", "..."],
       "timestamp_utc": "2026-07-02T12:34:56Z",
       "container_image_digest": "sha256:...",
       "container_git_commit": "abc1234"
     }
     ```
- **Halt on schema errors** (config parse fail, missing columns, empty dataset). Exit 1. SFN catches the ProcessingJob failure and routes to the SNS `PublishFailure` step (already wired in `analyzer_baseline_construct.py`).
- **Skip analyzers that fail but continue the rest** — not applicable here because a baseline run is one analyzer per job (`MONITOR_TYPE=bias` or `explainability`), unlike `ufx-monitor` which bundles multiple. If we later fold MQ/DQ baseline into the same container (Part D.4), we would inherit `ufx-monitor`'s per-analyzer isolation model.

Exit codes (contract with SFN):

| Code | Meaning | SFN handling |
|---|---|---|
| 0 | Success. `analysis.json` (or `constraints.json`/`statistics.json`) written. | Falls through to `HeadObject` step. |
| 1 | Config/dataset/model load failure. `failure.json` written. | SFN catch → SNS Publish → Fail. |
| 2 | Analyzer computed but output write failed. Rare. | Same catch. |

---

## Part C — Infra integration

### C.1 CDK construct — `UfxBaselineConstruct`

Two clean options:

**Option 1 — Add UFX branches inside existing `AnalyzerBaselineConstruct`.**
Small diff (~80 LOC). Preserves single dispatch point for all four monitor types. Downside: mixes closed-source Clarify config knobs with UFX config knobs in the same file during Phase 2 parallel-run.

**Option 2 — New `UfxBaselineConstruct` class, referenced from `AnalyzerBaselineConstruct` only for `MonitorType.BIAS` and `MonitorType.EXPLAINABILITY`.**
Larger diff (~180 LOC). Cleaner separation. Kills Clarify branches outright at Phase 3.

**Recommendation: Option 2.** The Clarify branches are going away — building the new construct as a peer, then deleting the old branches, is the cleaner arc. The SFN/EventBridge scaffolding (Validate → CreateProcessingJob.sync → HeadObject → Succeed with SNS-on-fail branch) is reused, just with a different image URI, container args, and IAM. Extract that scaffolding to a helper method `_build_processing_state_machine(image_uri, container_args, env, resources)` that both constructs call.

Files to edit:

| File | Change |
|---|---|
| `src/stacks/ufx_ml_monitoring_stack/constructs/analyzer_baseline_construct.py` | Route `BIAS`/`EXPLAINABILITY` to `UfxBaselineConstruct`. Delete `_CLARIFY_TYPES`, `_CLARIFY_IMAGE_BY_REGION`, and the Clarify branch of `_processing_inputs` / `_job_runtime_spec` once Phase 3 lands. |
| `src/stacks/ufx_ml_monitoring_stack/constructs/ufx_baseline_construct.py` | **New.** Peer class. Reuses the SFN scaffolding pattern. |
| `src/stacks/ufx_ml_monitoring_stack/constructs/__init__.py` | Export the new construct. |
| `src/common/monitor_containers/build_config.py` | New — image URI helper, mirrors the existing `ufx_monitor` image-URI resolver. |
| `src/common/inference_deployments/monitoring_configs.py` | Add `UfxBaselineConfig` pydantic model (name TBD to avoid clash with container's `UfxBaselineConfig`; likely `UfxBaselineCdkConfig`). |
| `.github/workflows/build-ufx-baseline-image.yml` | **New.** Mirrors the existing `build-ufx-monitor-image.yml`. |
| `src/stacks/ufx_ml_monitoring_stack/README.md` | Document the new dispatch matrix. |

Pseudocode for the new construct:

```python
# src/stacks/ufx_ml_monitoring_stack/constructs/ufx_baseline_construct.py
class UfxBaselineConstruct(Construct):
    """Bias / Explainability baseline via UFX-owned container.

    Same SFN topology as ``AnalyzerBaselineConstruct`` uses for MQ/DQ, but the
    Processing Job launches ``ufx-baseline`` from ML_ARTIFACT ECR instead of
    the AWS-published Clarify image.

    Attributes:
        baselines_bucket: S3 bucket for inputs + outputs.
        monitor_type: BIAS or EXPLAINABILITY.
        state_machine: Standard SFN state machine (EB target).
        s3_trigger_rule: EventBridge S3 → SFN rule.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        package_group_name: str,
        monitor_type: MonitorType,
        baseline_output_version: int,
        baselines_bucket: IBucket,
        baselines_kms_key: IKey,
        model_artefact_bucket: IBucket,          # NEW — where model.tar.gz lives
        model_artefact_key: str,                 # NEW — full key to the model.tar.gz
        config: BiasConfig | ExplainabilityConfig,
        image_uri: str,                          # ML_ARTIFACT ECR — ufx-baseline
        alerts_topic: ITopic | None = None,
    ) -> None:
        assert monitor_type in {MonitorType.BIAS, MonitorType.EXPLAINABILITY}
        # ... construct role, state machine, event rule ...

    def _job_environment(self) -> dict[str, str]:
        """UFX-baseline env block passed as CreateProcessingJob Environment.

        Returns:
            Env dict consumed by ufx_baseline.cli._load_env.
        """
        env = {
            "PACKAGE_GROUP_NAME": self.package_group_name,
            "BASELINE_VERSION": str(self.baseline_output_version),
            "MONITOR_TYPE": self.monitor_type.value,
        }
        if self.monitor_type is MonitorType.EXPLAINABILITY:
            env["MODEL_ADAPTER"] = self.config.model_adapter          # e.g. "bos_seq"
            env["MODEL_S3_URI"] = f"s3://{self.model_artefact_bucket.bucket_name}/{self.model_artefact_key}"
            env["SHAP_NSAMPLES"] = str(self.config.shap_nsamples)
            env["SHAP_BACKGROUND_K"] = str(self.config.shap_background_k)
        return env
```

The SFN definition is a straight lift of the existing `_create_state_machine` in `AnalyzerBaselineConstruct` — reuse the helper. Only differences:
- `AppSpecification.ImageUri` — points at ML_ARTIFACT ECR `ufx-baseline`.
- `ContainerArguments` — none (env-driven).
- `Environment` — the dict above.
- `ProcessingInputs` — one channel `dataset` at `/opt/ml/processing/input/data`, one channel `analysis_config` at `/opt/ml/processing/input/config`. Same shape Clarify already gets.

### C.2 IAM — baseline container role

Baseline container role (SageMaker Processing Job execution role) needs, and **only** needs:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadBaselineInputs",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::monitoring-baselines-965377249924-eu-west-1",
        "arn:aws:s3:::monitoring-baselines-965377249924-eu-west-1/monitoring-baselines/{pkg}/{type}/input/v{N}/*"
      ]
    },
    {
      "Sid": "WriteBaselineOutputs",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:AbortMultipartUpload"],
      "Resource": [
        "arn:aws:s3:::monitoring-baselines-965377249924-eu-west-1/monitoring-baselines/{pkg}/{type}/output/v{N}/*"
      ]
    },
    {
      "Sid": "ReadModelArtefact",
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": [
        "arn:aws:s3:::{model-artefact-bucket}/{model.tar.gz-key}"
      ]
    },
    {
      "Sid": "KmsDecryptBaselineAndModel",
      "Effect": "Allow",
      "Action": ["kms:Decrypt", "kms:DescribeKey"],
      "Resource": [
        "{baselines-cmk-arn}",
        "{model-artefact-cmk-arn}"
      ]
    },
    {
      "Sid": "KmsEncryptBaselineOutput",
      "Effect": "Allow",
      "Action": ["kms:Encrypt", "kms:GenerateDataKey"],
      "Resource": ["{baselines-cmk-arn}"]
    },
    {
      "Sid": "CloudWatchLogsWrite",
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents", "logs:CreateLogGroup"],
      "Resource": ["arn:aws:logs:eu-west-1:965377249924:log-group:/aws/sagemaker/ProcessingJobs*"]
    },
    {
      "Sid": "EcrPullImage",
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage"
      ],
      "Resource": "*"
    }
  ]
}
```

**Explicitly NOT granted** (vs. Clarify's execution role today):
- `sagemaker:InvokeEndpoint` — Clarify called a shadow endpoint for SHAP. We load the model in-container. Kills the cross-account endpoint plumbing described in assessment §3d.
- `sagemaker:CreateEndpoint` / `DeleteEndpoint` — Clarify sometimes provisioned a temporary endpoint. We never do.
- Any cross-account `sts:AssumeRole` — see C.3 for the *conditional* one.

### C.3 DS-side grants (transitional — pre-MPG migration)

For the pre-migration era where the model artefact still lives in `DS` (`714462557551`), ML_ARTIFACT (`965377249924`) needs cross-account read on the model. Two options:

**Option A — Resource policy on the DS bucket.** Add:
```json
{
  "Sid": "AllowMLArtifactBaselineRead",
  "Effect": "Allow",
  "Principal": {"AWS": "arn:aws:iam::965377249924:role/UfxBaseline-*"},
  "Action": ["s3:GetObject"],
  "Resource": "arn:aws:s3:::{ds-model-bucket}/models/bos_sess_seq_clf/{version}/model.tar.gz"
}
```
Plus a matching KMS key policy grant on the DS-side CMK for `kms:Decrypt`.

**Option B — sts:AssumeRole to a DS-side role that already has the reads.** Same mechanism `emit_baseline_inputs.py` uses today for cross-account writes (`BaselineUploaderRole`, per ml-iac#164). Symmetry win. Requires a `BaselineDownloaderRole` in DS with the S3+KMS reads on the model artefact.

**Recommendation:** Option B during the transitional period. It matches the pattern already ml-iac-managed for uploads, keeps DS-side changes to one role rather than N bucket policies (one per model), and dies naturally once MPG lives in ML_ARTIFACT and the assume-role step goes away.

### C.4 ECR image storage + CI/CD

Image lives in **ML_ARTIFACT ECR**, same repo pattern as `ufx-monitor`:
- Repo: `965377249924.dkr.ecr.eu-west-1.amazonaws.com/ufx-baseline`
- Tag scheme: `<short-commit-sha>` for every push to `main`; `latest` moves.
- CDK reads the tag from an SSM param or ECR image lookup — same helper `ufx-monitor` uses.

GitHub Actions workflow — mirror `build-ufx-monitor-image.yml`:

```yaml
# .github/workflows/build-ufx-baseline-image.yml
name: Build ufx-baseline image
on:
  push:
    branches: [main]
    paths: ['src/monitor_containers/ufx_baseline/**']
  workflow_dispatch:
permissions:
  id-token: write   # OIDC for aws-actions/configure-aws-credentials
  contents: read
jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::965377249924:role/GH-EcrPush-UfxBaseline
          aws-region: eu-west-1
      - uses: aws-actions/amazon-ecr-login@v2
      - name: Build + push
        working-directory: src/monitor_containers/ufx_baseline
        run: |
          IMAGE_URI=965377249924.dkr.ecr.eu-west-1.amazonaws.com/ufx-baseline:${{ github.sha }}
          docker build -t "$IMAGE_URI" .
          docker push "$IMAGE_URI"
          # Move :latest — CDK reads by digest in prod so this is cosmetic.
          docker tag "$IMAGE_URI" 965377249924.dkr.ecr.eu-west-1.amazonaws.com/ufx-baseline:latest
          docker push 965377249924.dkr.ecr.eu-west-1.amazonaws.com/ufx-baseline:latest
```

Adds a scan gate (Trivy or `docker scout`) as a follow-on ticket — copy from `ufx-monitor` if it has one, otherwise track under `.claude/findings.md`.

### C.5 SFN retry policy

Baseline runs are triggered by S3 `Object Created` on the input prefix. Two baselines dropping in the same second is the common failure — that hit the 1 rps `CreateProcessingJob` quota (assessment §3c).

Reuse the existing retry block from `AnalyzerBaselineConstruct._create_state_machine`:

```json
"Retry": [
  {
    "ErrorEquals": ["SageMaker.AmazonSageMakerException", "States.TaskFailed"],
    "IntervalSeconds": 30,
    "MaxAttempts": 3,
    "BackoffRate": 2.0,
    "JitterStrategy": "FULL"
  },
  {
    "ErrorEquals": ["States.ALL"],
    "IntervalSeconds": 10,
    "MaxAttempts": 2,
    "BackoffRate": 2.0
  }
]
```

Slight tweak vs. today: bump `IntervalSeconds` from 5→30 on the first block. Second attempt at 30s, third at 60s (2× backoff), fourth at 120s. At 1 rps hard limit, even 30 concurrent baseline uploads clear the throttle inside ~2 min. Keeps the throttle window well below the SFN 2-hour timeout already configured.

---

## Part D — Migration plan

### D.1 Phase 0 — Preflight

1. Build `ufx-baseline` image locally.
2. Push to ML_ARTIFACT ECR under a `dev-<commit>` tag.
3. Unit-test the container against a real `bos_sess_seq_clf` dataset snapshot. Concretely — mount a copy of the latest `bos_seq` baseline input from S3 to `/opt/ml/processing/input/data`, run `docker run --rm -e MONITOR_TYPE=bias -e PACKAGE_GROUP_NAME=bos-sess-seq-clf -e BASELINE_VERSION=99 ufx-baseline:dev-<sha>` locally, diff the resulting `analysis.json` against Clarify's most recent good run.
4. Green: `smclarify` numeric outputs match Clarify's to 4 decimal places on the same input. SHAP top-10 features overlap ≥ 80%.

Owner: 1 engineer. Estimate: 1-2 days.

### D.2 Phase 1 — Parallel run

1. Deploy `UfxBaselineConstruct` **alongside** the existing Clarify path in `AnalyzerBaselineConstruct`. Both wired to the same S3 input prefix, writing to distinct output subprefixes (`.../output/v{N}/` for Clarify, `.../output-v2/v{N}/` for `ufx-baseline`).
2. Every baseline upload triggers both SFNs. Ignore transient throttles — Phase 1 tolerates them.
3. Comparison script (once-per-week): pull both `analysis.json` outputs, compare metric-by-metric.
   - **Green criteria:** bias numeric values match to 4 decimal places (smclarify is deterministic — differences at this tolerance indicate a wrapper bug in our container).
   - **Green criteria:** SHAP `global_shap_values` top-10 rank overlap ≥ 90% per class. Absolute values will differ (different `nsamples` / `background_size` between our KernelExplainer call and Clarify's) — rank stability is the honest metric.
4. Two weeks of green comparisons → sign-off for Phase 2.

Owner: 1 engineer + 30 min/week comparison chore.

### D.3 Phase 2 — Cutover

1. Flip the EventBridge target: `S3TriggerRule<Bias|Explain>` now points at the UFX SFN, not the Clarify SFN.
2. Keep Clarify path deployed but idle for 2 weeks. Rollback plan: flip the EB target back.
3. Monitoring: alarm on `ufx-baseline` SFN failure rate > 0 over 24h. Alarm on missing output S3 object.

Owner: 1 engineer, plus deploy window.

### D.4 Phase 3 — Rip out

1. Delete Clarify branches from `analyzer_baseline_construct.py`. Delete `_CLARIFY_TYPES`, `_CLARIFY_IMAGE_BY_REGION`, Clarify `_processing_inputs`, Clarify `_job_runtime_spec`.
2. Delete Clarify-specific fields from ml-core's `emit_baseline_inputs.py` analysis_config generator (JMESPath features, `content_template`, `headers` list, `label_headers`).
3. Consider (deferred): fold MQ + DQ baseline into the same `ufx-baseline` image. Rationale — MQ/DQ still use the AWS Model Monitor analyzer image, which is closed-source and shares failure modes with Clarify. But that image is more benign (no config gymnastics), so the priority is lower. Track as a separate ticket.

Owner: 1 engineer. Estimate: half-day.

---

## Part E — Cost + operability

### E.1 Compute sizing

| Job | Instance | Rationale | Approx runtime | Cost (eu-west-1, on-demand) |
|---|---|---|---|---|
| Bias | `ml.m5.large` | smclarify is pandas-heavy but not compute-heavy. 2 vCPU / 8 GB. | 1-3 min | ~$0.12/hr → **~$0.006/run** |
| Explainability (SHAP) | `ml.m5.2xlarge` | KernelExplainer is CPU-bound (8 cores → 8-way parallel via numba). 200 explain rows × 100 nsamples × 50 background = ~10⁶ predict_proba calls. | ~10 min at nsamples=100, ~30 min at nsamples=500 | ~$0.48/hr → **~$0.08/run** at nsamples=100 |

Reference AWS pricing: [SageMaker Processing Pricing](https://aws.amazon.com/sagemaker/pricing/) — `ml.m5.large` $0.1152/hr, `ml.m5.2xlarge` $0.4608/hr eu-west-1 on-demand.

### E.2 Total monthly cost

Assume 4 models × 1 baseline per week × (bias + explain) = 32 runs/month.

- 16 bias runs @ $0.006 = **$0.10/month**
- 16 explainability runs @ $0.08 = **$1.28/month**
- SFN + EB + CW Logs — negligible (<$1/month).

**Total: ~$2.50/month** across all baseline compute. Same order as Clarify was — same compute class, same runtime. The dollar case for this rewrite is not "cheaper compute"; it is "the compute we already paid for is now debuggable and owned."

### E.3 Observability

- **CW Logs.** Each Processing Job creates a log stream under `/aws/sagemaker/ProcessingJobs`. `ufx-baseline` emits structured JSON logs (mirroring `ufx_monitor`'s `logging.basicConfig` pattern).
- **SFN execution history.** One execution per baseline upload. Full state history in the SFN console, including the `CreateProcessingJob.sync` polled Describe payload.
- **CW metrics namespace.** `ufx/monitoring/v2/baseline-*`. Metrics:
  - `baseline_run_success` (Count, 1 per successful run)
  - `baseline_run_failed_config` / `_dataset` / `_model` / `_compute` / `_write` (Count, 1 per failure by phase)
  - `baseline_compute_duration_seconds` (Seconds, per run)
  - `shap_nsamples_used` (Count) — for cost auditing
  - Dimensions: `PackageGroupName`, `MonitorType`, `BaselineVersion`.
- Container emits these directly to CW at end-of-job via boto3 — same pattern `ufx_monitor` uses.
- **Dashboard.** Add a "Baselines" tab to the existing `UfxSiloDashboard` (see task #72). Widgets: run count per week per model, failure count by phase, mean compute duration, latest baseline age. All SEARCH-expression widgets (per refactor #59) so they self-discover new packages.

### E.4 On-call scenarios

| Scenario | Signal | Mitigation |
|---|---|---|
| `CreateProcessingJob` throttle | SFN retries visible in state history; if all 5 retries exhaust, SNS `PublishFailure`. | Backoff already tuned (C.5). If we see repeats, extend `MaxAttempts` and/or serialise concurrent uploads via SFN Wait step. |
| Model load failure (`model.tar.gz` corrupt / missing) | `failure.json` `phase="model-load"`. SNS Publish subject: `Baseline failure: {pkg} explainability`. | Confirm S3 object exists + KMS grants in place. Common causes: MPG version reg step failed, DS-side role expired. |
| S3 KMS Access Denied | `failure.json` `phase="dataset-load"` or `"output-write"`. Boto3 raises `ClientError` with `AccessDenied`. | Cross-account grants — check DS-side role trust (Option B in C.3) or bucket/KMS policy. Also verify the SageMaker execution role gets `kms:Decrypt`. |
| SHAP OOM | Container OOMs → SageMaker marks job `Failed`. No `failure.json` written (process died pre-flush). CW logs show `MemoryError` before the kill. | Reduce `SHAP_NSAMPLES` or `SHAP_BACKGROUND_K`. Or bump instance to `ml.m5.4xlarge` (32 GB). Cost jumps to ~$0.15/run. |
| Baseline output written but empty / bad shape | `HeadObject` step succeeds (object exists); `ufx-monitor` will fail to parse it hours later. | Add a shape-check step in the SFN — a Lambda that opens `analysis.json` and validates the pydantic schema before Succeed. Track as a follow-up hardening ticket. |

Auth-reality note (per Manager brief): SSO / claude-br sessions expire during long-running dev-loop iterations. Local `docker run` unit tests survive re-auth because they don't hold AWS credentials for long (only long enough to download the input dataset). SFN + Processing Job execution is entirely inside AWS, immune to local-session expiry. **No auth-survival changes needed.**

---

## Part F — Risks + open questions

### F.1 What if AWS unpublishes `smclarify` from PyPI?

Nonzero risk given the closed-access change. Fallbacks:

- **Vendored fork.** Fork `aws/amazon-sagemaker-clarify` to `urbanfoxai/smclarify` and reference the fork's `src/smclarify` via a path dependency in `pyproject.toml`. Apache-2.0 license permits this.
- **Reimplement the formulas.** Each metric is ~5-10 LOC and is arithmetic on labelled DataFrames. Formulas per [Clarify metric doc](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-measure-data-bias.html):
  - **CI** (Class Imbalance): `(n_a - n_d) / (n_a + n_d)` where n_a, n_d are counts in advantaged / disadvantaged facet.
  - **DPL** (Difference in Positive Proportion in Labels): `q_a - q_d` where q_x = P(y=1 | facet=x).
  - **KL** divergence: `sum(P(y|a) log(P(y|a)/P(y|d)))` over outcome classes.
  - **JS** divergence: `0.5·KL(P_a || M) + 0.5·KL(P_d || M)` with `M = 0.5·(P_a + P_d)`.
  - **TVD**: `0.5·sum(|P_a - P_d|)`.
  - **LP**: `(sum(|P_a - P_d|^p))^(1/p)`, default p=2.
  - **KS**: max over cumulative label distributions.
  - **DI** (Disparate Impact, post-training): `q'_d / q'_a`.
  - **DPPL**: same as DPL but on predicted labels.
  - Sub-hour reimplementation.

We do not vendor at Phase 1. Track as a risk with two mitigations at hand.

### F.2 SHAP on a BiGRU consuming session-level flat features

Per this week's flatten fix (task #88 / squad ml-core BOS_SEQ baseline flattener), BOS_SEQ now takes **session-aggregate flat features**. Each row is one session. The BiGRU inside the model still runs internally, but from the outside it looks like `predict_proba: [n, f] -> [n, 3]`. Perfect for `KernelExplainer`.

If the model reverts to true sequence input (T timesteps per session), we'd need a wrapper that reshapes `[n, f]` back to `[n, T, f_per_step]` inside `predict_proba`. But the current model shape makes SHAP straightforward. The wrapper in B.7 (`predict_proba` doing a `unsqueeze(1)` for the BiGRU's expected `[n, T, f]` shape) is the minimum needed.

**Open question:** Is per-row SHAP against the flattened features scientifically meaningful when the model internally attends over a sequence structure? The answer per the SHAP paper and Clarify docs: yes, provided `predict_proba` is deterministic and the flattened features are what the model *actually consumes*. Since the flatten fix does exactly that, SHAP output is honest. If a future model version returns to true sequence input and preprocessor-driven flattening, we may want to attribute over the raw event stream instead — deferred to a follow-up.

### F.3 Sample size vs runtime

- 100 nsamples × 200 explain-rows × 50 background = 10⁶ `predict_proba` calls.
- BiGRU forward pass on 1 row ~5 ms CPU (measured on `ml.m5.2xlarge` for a similar sized model). 8 vCPU × 8-way batching = ~0.6 ms amortised.
- Total: **~10 min** at nsamples=100.

Going to nsamples=500:
- 5× the calls → ~50 min. `ml.m5.2xlarge` at $0.4608/hr → ~$0.38/run.
- Still under $10/month total baseline cost. **We can absolutely go to nsamples=500 without doubling anything meaningful** — the "double" is on wall-clock time, not on monthly dollars.

Recommendation: start at nsamples=100 for the Phase 1 parallel-run (fast iteration). Bump to 500 post-cutover once shape is stable.

### F.4 Model version pinning — when does baseline recompute?

Current trigger: EventBridge on S3 `Object Created` in the input prefix. That fires whenever ml-core's training pipeline emits a new baseline input. Ml-core's pipeline emits a baseline input when `if_steps` (eval gate) passes — i.e. per new registered MP version.

So: **baseline recomputes on every MP registration, not on schedule.** This is the correct trigger. `MODEL_S3_URI` is passed via CDK — it currently points at the DS-account model bucket by MPG version. When the ml-core pipeline registers a new version, it also emits the baseline input, which fires the baseline SFN, which loads the *new* model.tar.gz from the same version.

Open question: what if the endpoint deployment lags MP registration? Answer: fine — the baseline is a property of the MP, not the endpoint. Downstream `ufx-monitor` compares captured inference against the baseline for its own MP version.

### F.5 Provenance sidecar

Yes — emit `_provenance.json` alongside `analysis.json`. Shape:

```json
{
  "schema_version": 1,
  "monitor_type": "explainability",
  "package_group_name": "bos-sess-seq-clf",
  "baseline_version": 3,
  "model_s3_uri": "s3://.../model.tar.gz",
  "model_etag": "abc...",
  "dataset_row_count": 12045,
  "container_image_uri": "965377249924.dkr.ecr.eu-west-1.amazonaws.com/ufx-baseline:<sha>",
  "container_image_digest": "sha256:...",
  "container_git_commit": "abc1234",
  "smclarify_version": "0.5",
  "shap_version": "0.52.0",
  "torch_version": "2.x",
  "started_at_utc": "2026-07-02T12:00:00Z",
  "ended_at_utc": "2026-07-02T12:09:34Z",
  "shap_config": {"nsamples": 100, "background_size": 50, "explain_size": 200}
}
```

Matches ml-core's baseline emitter provenance style (see `emit_baseline_inputs.py` header). Gives us the "which container image, which model file, which library version" audit trail Clarify never exposed.

---

## Part G — Summary of deliverables

| Deliverable | Path | Est LOC |
|---|---|---|
| Container Python | `src/monitor_containers/ufx_baseline/ufx_baseline/**` | ~350 |
| Container tests | `src/monitor_containers/ufx_baseline/tests/**` | ~250 |
| Dockerfile + entrypoint | `src/monitor_containers/ufx_baseline/{Dockerfile,entrypoint.sh}` | ~40 |
| pyproject.toml + uv.lock | `src/monitor_containers/ufx_baseline/pyproject.toml` | ~40 |
| README | `src/monitor_containers/ufx_baseline/ufx_baseline/README.md` | ~150 |
| CDK — new construct | `src/stacks/ufx_ml_monitoring_stack/constructs/ufx_baseline_construct.py` | ~180 |
| CDK — dispatch tweak | `src/stacks/ufx_ml_monitoring_stack/constructs/analyzer_baseline_construct.py` | ~30 diff |
| CDK — config dataclass | `src/common/inference_deployments/monitoring_configs.py` | ~40 |
| CI workflow | `.github/workflows/build-ufx-baseline-image.yml` | ~50 |
| DS-side role (transitional) | Bespoke ticket in DS account | ~60 (Terraform or manual) |
| **Total** | | **~1,190 LOC** |

Squad-day cost estimate from assessment §6 (~1 day) is optimistic; realistic estimate given the Phase 1 parallel-run + comparison harness + docs is **3-5 engineer-days** including infra, tests, docs, and the two-week parallel-run watch. Aligns with what §7 sketches.

---

## References

- AWS — [Clarify availability change (deprecation notice)](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html)
- AWS — [Troubleshoot SageMaker Clarify Processing Jobs](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-processing-job-run-troubleshooting.html)
- AWS — [SageMaker service quotas (CreateProcessingJob rate)](https://docs.aws.amazon.com/general/latest/gr/sagemaker.html)
- AWS — [aws-samples/sample-aiops-on-amazon-sagemakerai (recommended replacement)](https://github.com/aws-samples/sample-aiops-on-amazon-sagemakerai/tree/main/monitoring)
- AWS — [aws/amazon-sagemaker-clarify](https://github.com/aws/amazon-sagemaker-clarify)
- AWS — [smclarify bias/report.py source](https://github.com/aws/amazon-sagemaker-clarify/blob/master/src/smclarify/bias/report.py)
- AWS — [Clarify SHAP values doc (confirms shap engine)](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-shapley-values.html)
- AWS — [Clarify measure data bias formulas](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-measure-data-bias.html)
- AWS — [Clarify analysis config json shape](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-config-json-analysis.html)
- SHAP — [KernelExplainer API](https://shap.readthedocs.io/en/latest/generated/shap.KernelExplainer.html)
- SHAP — [PyPI (shap 0.52.0)](https://pypi.org/project/shap/)
- Evidently — [evidentlyai/evidently](https://github.com/evidentlyai/evidently)
- WhyLabs — [whylabs/whylogs](https://github.com/whylabs/whylogs)
- UFX internal — [SM_MODEL_MONITOR_ASSESSMENT.md](SM_MODEL_MONITOR_ASSESSMENT.md)
- UFX internal — [ufx-monitor container README](../../monitoring-custom-container-explore/src/monitor_containers/ufx_monitor/README.md)
- UFX internal — [`analyzer_baseline_construct.py`](../../monitoring-custom-container-explore/src/stacks/ufx_ml_monitoring_stack/constructs/analyzer_baseline_construct.py)
- UFX internal — [ml-core `emit_baseline_inputs.py`](../../../../ml-core/projects/bos_sess_seq_clf/src/emit_baseline_inputs.py)
- UFX internal — [ml-core BOS_SEQ inference handler](../../../../ml-core/projects/bos_sess_seq_clf/src/inference.py)
