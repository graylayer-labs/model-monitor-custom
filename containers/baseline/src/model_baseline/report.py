"""Clarify-compatible ``analysis.json`` output schema."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class BiasFacetMetric(BaseModel):
    """A single bias metric emitted for a facet.

    Attributes:
        name: Bias metric code (e.g. ``"CI"``, ``"DPL"``).
        value: Metric value.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    value: float


class BiasFacetReport(BaseModel):
    """Per-facet bias metrics for a given label value.

    Attributes:
        label_value_or_threshold: Positive-label description.
        facets: Mapping from facet name to its list of metrics.
    """

    model_config = ConfigDict(extra="forbid")

    label_value_or_threshold: str
    facets: dict[str, list[BiasFacetMetric]]


class PreTrainingBias(BaseModel):
    """Pre-training bias report block.

    Attributes:
        label: Label column name.
        label_value_or_threshold: Positive-label description.
        facets: Mapping from facet name to its list of metrics.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    label_value_or_threshold: str
    facets: dict[str, list[BiasFacetMetric]]


class GlobalShapValues(BaseModel):
    """Aggregated global SHAP importance per feature.

    Attributes:
        values: Feature-name to importance.
    """

    model_config = ConfigDict(extra="forbid")

    values: dict[str, float]


class Explanations(BaseModel):
    """Explanations block. Currently only Kernel SHAP.

    Attributes:
        kernel_shap: Global SHAP block, or ``None`` if unavailable.
    """

    model_config = ConfigDict(extra="forbid")

    kernel_shap: GlobalShapValues | None = None


class AnalysisReport(BaseModel):
    """Top-level Clarify-compatible analysis report.

    Attributes:
        version: Schema version.
        pre_training_bias: Bias block, present for bias baselines.
        explanations: Explanations block, present for explainability baselines.
        package_group_name: Provenance — Model Package Group.
        baseline_version: Provenance — baseline version integer.
        monitor_type: ``"BIAS"`` or ``"EXPLAINABILITY"``.
        generated_at_utc: ISO-8601 UTC timestamp of report generation.
        container_image_digest: Digest of the baseline container image.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    pre_training_bias: PreTrainingBias | None = None
    explanations: Explanations | None = None
    package_group_name: str
    baseline_version: int
    monitor_type: Literal["BIAS", "EXPLAINABILITY"]
    generated_at_utc: str
    container_image_digest: str
