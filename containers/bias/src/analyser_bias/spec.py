"""BiasAnalyser config schema.

Duplicates a subset of :mod:`model_baseline.specs.BiasSpec` so the bias
container does not depend on the baseline package. Uses Pydantic v2 with
``extra="forbid"`` per ADR 004.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BiasMethod = Literal["CI", "DPL", "KL", "JS", "LP", "TVD", "KS", "CDDL"]
PRE_TRAINING_METHODS: frozenset[str] = frozenset({"CI", "DPL", "KL", "JS", "LP", "TVD", "KS", "CDDL"})
POST_TRAINING_METHODS: frozenset[str] = frozenset({"DPPL", "DI", "DCA", "DCR", "RD", "DAR", "DRR", "AD", "TE"})


class Facet(BaseModel):
    """Protected-attribute facet.

    Attributes:
        name: Column name of the facet in the dataset.
        values: Sensitive values within the facet column.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    values: list[str | int | float | bool]


class BiasSpec(BaseModel):
    """Bias analyser configuration.

    Attributes:
        schema_version: Semver of the spec shape.
        dataset_input: Name of the input in ``AnalyserInputs.paths`` holding
            the parquet dataset (features + label + optional predictions).
        label_column: Name of the label column.
        positive_label_values: Values of ``label_column`` treated as positive.
        predicted_label_column: Optional column holding model predictions.
            When set, post-training methods are also computed.
        facets: Facets to compute bias against.
        methods: Bias metric codes to compute.
        thresholds: Per-metric absolute-value cutoff — a metric whose ``abs``
            value exceeds its threshold contributes to ``violation_count``
            and pushes severity to ``warn``.
        severity_alert_thresholds: Optional per-metric cutoff that escalates
            severity to ``alert`` when any metric's ``abs`` value exceeds it.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    dataset_input: str = "dataset"
    label_column: str
    positive_label_values: list[str | int | float | bool]
    predicted_label_column: str | None = None
    facets: list[Facet]
    methods: list[BiasMethod] = ["CI", "DPL", "KL", "JS"]
    thresholds: dict[str, float] = Field(default_factory=dict)
    severity_alert_thresholds: dict[str, float] | None = None

    @model_validator(mode="after")
    def _unique_facet_names(self) -> BiasSpec:
        """Reject duplicate facet names — they collide in the report keys.

        Returns:
            The validated model.

        Raises:
            ValueError: If two facets share a name.
        """
        names = [f.name for f in self.facets]
        if len(names) != len(set(names)):
            msg = "Facet names must be unique"
            raise ValueError(msg)
        return self
