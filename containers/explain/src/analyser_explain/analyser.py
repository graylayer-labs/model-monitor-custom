"""Real :class:`ExplainAnalyser` — SHAP-backed global explainability.

The base image fetches inputs + config from S3 before ``compute`` runs. This module
performs zero AWS SDK calls: it consumes local file paths and returns a structured
:class:`~mmc_base.contract.AnalyserOutput`.

Global SHAP importances are emitted as per-feature analyser metrics
(``shap/<feature>``) — capped to the top-K most important features to keep
CloudWatch cardinality bounded. The full ranking still lands in ``payload`` as a
Clarify-compatible :class:`~model_baseline.report.AnalysisReport`.

Explainability is descriptive, not gating: ``outcome`` is always
:attr:`~mmc_base.contract.Outcome.succeeded` on a clean run and ``severity`` is
``info``. No violation semantics apply.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd
import shap
from mmc_base.contract import AnalyserOutput, Outcome, Severity
from model_baseline.report import AnalysisReport, Explanations, GlobalShapValues
from model_baseline.specs import AggMethod
from pydantic import BaseModel, ConfigDict, Field

from analyser_explain.adapters import Framework, load_adapter

if TYPE_CHECKING:
    from mmc_base.contract import AnalyserInputs
    from model_baseline.adapters.base import ModelAdapter

ExplainAggMethod = Literal["mean_abs", "mean", "max_abs"]

DEFAULT_TOP_K = 20
DEFAULT_PACKAGE_GROUP_NAME = "unknown"
DEFAULT_BASELINE_VERSION = 0
_MULTICLASS_TENSOR_NDIM = 3


class ExplainAnalyserSpec(BaseModel):
    """Config schema for :class:`ExplainAnalyser`.

    Mirrors Phase 1's :class:`~model_baseline.specs.ExplainSpec` and adds the
    ``framework`` + ``model_uri`` fields needed to load the artefact locally.

    Attributes:
        framework: Which adapter to load — ``"sklearn"`` or ``"xgboost"``.
        model_uri: Local path to the model artefact (base image has fetched it).
        features_input: Name of the input in ``inputs.paths`` that holds features.
        label_column: Optional label column to drop from the feature frame.
        num_samples: Kernel SHAP samples per prediction.
        background_size: Background dataset size for Kernel SHAP.
        agg_method: How per-instance SHAP values aggregate to global.
        top_k: Cap for per-feature CloudWatch metrics (``shap/<feature>``).
        package_group_name: Provenance for the payload report.
        baseline_version: Provenance for the payload report.
    """

    model_config = ConfigDict(extra="forbid")

    framework: Framework
    model_uri: str
    features_input: str = "features"
    label_column: str | None = None
    num_samples: int = Field(default=100, gt=0, le=10_000)
    background_size: int = Field(default=50, gt=0, le=500)
    agg_method: ExplainAggMethod = "mean_abs"
    top_k: int = Field(default=DEFAULT_TOP_K, gt=0, le=500)
    package_group_name: str = DEFAULT_PACKAGE_GROUP_NAME
    baseline_version: int = DEFAULT_BASELINE_VERSION


def _aggregate(shap_values: np.ndarray, method: ExplainAggMethod | AggMethod) -> np.ndarray:
    """Reduce a ``[n_rows, n_features]`` SHAP array to a per-feature vector.

    Args:
        shap_values: Raw SHAP values.
        method: Aggregation method.

    Returns:
        1-D array of length ``n_features``.

    Raises:
        ValueError: If ``method`` is not recognised.
    """
    if method == "mean_abs":
        return np.abs(shap_values).mean(axis=0)
    if method == "mean":
        return shap_values.mean(axis=0)
    if method == "max_abs":
        return np.abs(shap_values).max(axis=0)
    msg = f"Unknown agg_method {method!r}"
    raise ValueError(msg)


def _to_2d_shap(raw: Any, n_classes: int) -> np.ndarray:  # noqa: ANN401 — shap returns a union of shapes
    """Normalise SHAP output into a ``[n_rows, n_features]`` array.

    Multiclass explainers return either a list of per-class arrays or a 3-D
    tensor ``[n_rows, n_features, n_classes]``. This flattens per-class importance
    by taking the mean absolute contribution across classes so the global report
    is a single ranking. Binary and regression outputs already come as
    ``[n_rows, n_features]``.

    Args:
        raw: SHAP values as returned by ``shap.Explainer``.
        n_classes: Number of model classes (used for shape disambiguation).

    Returns:
        A ``[n_rows, n_features]`` numpy array.
    """
    arr = raw.values if hasattr(raw, "values") else raw
    if isinstance(arr, list):
        stacked = np.stack([np.abs(np.asarray(a)) for a in arr], axis=-1)
        return stacked.mean(axis=-1)
    arr = np.asarray(arr)
    if arr.ndim == _MULTICLASS_TENSOR_NDIM:
        return np.abs(arr).mean(axis=-1)
    _ = n_classes
    return arr


def _compute_shap(adapter: ModelAdapter, framework: Framework, sample: pd.DataFrame, background: pd.DataFrame) -> Any:  # noqa: ANN401 — shap returns a union of shapes
    """Run the framework-appropriate SHAP explainer.

    Args:
        adapter: Loaded model adapter.
        framework: ``"sklearn"`` or ``"xgboost"``.
        sample: Rows to explain.
        background: Background dataset for Kernel SHAP.

    Returns:
        Raw SHAP output as returned by the chosen explainer.
    """
    if framework == "xgboost":
        booster = adapter._booster  # ty: ignore[unresolved-attribute]
        return shap.TreeExplainer(booster).shap_values(sample)
    return shap.KernelExplainer(adapter.predict_proba, background)(sample, silent=True)


class ExplainAnalyser:
    """SHAP-backed global explainability analyser."""

    def compute(self, inputs: AnalyserInputs, config: dict[str, Any]) -> AnalyserOutput:
        """Compute global SHAP importances.

        Args:
            inputs: Materialised inputs. ``paths[spec.features_input]`` must point
                to a parquet features file.
            config: Parsed analyser config — validated against
                :class:`ExplainAnalyserSpec`.

        Returns:
            :class:`AnalyserOutput` with per-feature metrics + Clarify report payload.
        """
        started = datetime.now(UTC)
        spec = ExplainAnalyserSpec.model_validate(config)
        adapter = load_adapter(spec.model_uri, spec.framework)

        features = self._load_features(inputs, spec)
        headers = adapter.feature_headers() or list(features.columns)
        features = features[headers]

        importances = self._compute_importances(adapter, spec, features, headers)
        top_k_metrics = self._top_k_metrics(importances, spec.top_k)
        payload = self._build_payload(importances, spec).model_dump()

        ended = datetime.now(UTC)
        return AnalyserOutput(
            outcome=Outcome.succeeded,
            severity=Severity.info,
            violation_count=0,
            analyser_metrics=top_k_metrics,
            run_started_at=started,
            run_ended_at=ended,
            payload=payload,
        )

    @staticmethod
    def _compute_importances(
        adapter: ModelAdapter,
        spec: ExplainAnalyserSpec,
        features: pd.DataFrame,
        headers: list[str],
    ) -> dict[str, float]:
        """Run SHAP and reduce to a feature → importance mapping.

        Args:
            adapter: Loaded model adapter.
            spec: Validated spec.
            features: Full feature dataframe.
            headers: Ordered feature column names.

        Returns:
            Ordered ``{feature: value}`` mapping.
        """
        background = shap.utils.sample(features, min(spec.background_size, len(features)), random_state=0)
        sample = shap.utils.sample(features, min(spec.num_samples, len(features)), random_state=0)
        raw = _compute_shap(adapter, spec.framework, sample, background)
        n_classes = max(len(adapter.class_labels()), 2)
        agg = _aggregate(_to_2d_shap(raw, n_classes), spec.agg_method)
        return dict(zip(headers, [float(v) for v in agg], strict=True))

    @staticmethod
    def _load_features(inputs: AnalyserInputs, spec: ExplainAnalyserSpec) -> pd.DataFrame:
        """Load and preprocess the features parquet.

        Args:
            inputs: Analyser inputs.
            spec: Validated spec.

        Returns:
            Feature dataframe with the label column dropped when configured.
        """
        path = inputs.paths.get(spec.features_input) or Path(spec.model_uri).with_suffix(".parquet")
        frame = pd.read_parquet(path)
        if spec.label_column and spec.label_column in frame.columns:
            frame = frame.drop(columns=[spec.label_column])
        return frame

    @staticmethod
    def _top_k_metrics(importances: dict[str, float], top_k: int) -> dict[str, float]:
        """Return the top-K importances keyed as ``shap/<feature>``.

        Args:
            importances: Feature-name → aggregated SHAP value.
            top_k: Maximum number of metrics to emit.

        Returns:
            Trimmed CloudWatch-safe metric dict.
        """
        ranked = sorted(importances.items(), key=lambda kv: abs(kv[1]), reverse=True)
        return {f"shap/{name}": value for name, value in ranked[:top_k]}

    @staticmethod
    def _build_payload(importances: dict[str, float], spec: ExplainAnalyserSpec) -> AnalysisReport:
        """Build the Clarify-compatible payload report.

        Args:
            importances: Full feature → importance mapping.
            spec: Validated spec (for provenance fields).

        Returns:
            :class:`AnalysisReport` ready for JSON dump.
        """
        return AnalysisReport(
            explanations=Explanations(kernel_shap=GlobalShapValues(values=importances)),
            package_group_name=spec.package_group_name,
            baseline_version=spec.baseline_version,
            monitor_type="EXPLAINABILITY",
            generated_at_utc=datetime.now(UTC).isoformat(),
            container_image_digest="",
        )
