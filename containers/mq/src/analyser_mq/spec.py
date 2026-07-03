"""MqAnalyser config schema.

Pydantic v2 with ``extra="forbid"`` per ADR 004.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MqSpec(BaseModel):
    """Model-quality analyser configuration.

    Attributes:
        schema_version: Semver of the spec shape.
        predictions_input: Name of the input in ``AnalyserInputs.paths``
            holding the parquet predictions frame (label + prediction +
            optional probability columns).
        problem_type: ``binary`` or ``multiclass``.
        label_column: Ground-truth label column name.
        prediction_column: Hard-label prediction column name.
        probability_columns: For binary, the single positive-class
            probability column (one entry). For multiclass, per-class
            probability columns (unused for AUC today). ``None`` skips AUC.
        positive_label: Positive label value for binary AUC. Ignored when
            ``problem_type == "multiclass"``.
        baseline_metrics: Snapshot of prior metric values to compare
            against, e.g. ``{"accuracy": 0.85, "f1_macro": 0.82}``.
        degradation_thresholds: Per-metric maximum allowed drop. Violation
            when ``current < baseline - threshold``.
        severity_threshold: Violation count at or above which severity is
            escalated to ``alert``. Below this, violations produce ``warn``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    predictions_input: str = "predictions"
    problem_type: Literal["binary", "multiclass"]
    label_column: str
    prediction_column: str
    probability_columns: list[str] | None = None
    positive_label: str | int | float | bool | None = None
    baseline_metrics: dict[str, float] = Field(default_factory=dict)
    degradation_thresholds: dict[str, float] = Field(default_factory=dict)
    severity_threshold: int = 2
