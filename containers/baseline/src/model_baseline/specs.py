"""Analyzer specification schemas (bias + explainability)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BiasMethod = Literal["CI", "DPL", "KL", "JS", "LP", "TVD", "KS", "CDDL"]
AggMethod = Literal["mean_abs", "mean", "median"]


class Facet(BaseModel):
    """A protected-attribute facet used in bias analysis.

    Attributes:
        name: Column name of the facet in the input dataset.
        values: Values considered the "sensitive" group.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    values: list[str | int]


class BiasSpec(BaseModel):
    """Bias analysis specification.

    Attributes:
        label_column: Name of the label column in the input dataset.
        positive_label_values: Values of ``label_column`` treated as positive.
        facets: Protected-attribute facets to compute metrics against.
        methods: Bias metric codes to compute.
    """

    model_config = ConfigDict(extra="forbid")

    label_column: str
    positive_label_values: list[str | int]
    facets: list[Facet]
    methods: list[BiasMethod] = ["CI", "DPL", "KL", "JS"]

    @model_validator(mode="after")
    def _unique_facet_names(self) -> BiasSpec:
        """Reject duplicate facet names — they collide in the report keys.

        Returns:
            The validated model instance.

        Raises:
            ValueError: If two facets share a name.
        """
        names = [f.name for f in self.facets]
        if len(names) != len(set(names)):
            msg = "Facet names must be unique"
            raise ValueError(msg)
        return self


class ExplainSpec(BaseModel):
    """Explainability (Kernel SHAP) specification.

    Attributes:
        num_samples: Number of Kernel SHAP samples per prediction.
        background_size: Background dataset size for SHAP.
        agg_method: How per-instance SHAP values are aggregated to global.
        save_local_shap_values: Whether to persist per-row SHAP values.
    """

    model_config = ConfigDict(extra="forbid")

    num_samples: int = Field(default=100, gt=0, le=10_000)
    background_size: int = Field(default=50, gt=0, le=500)
    agg_method: AggMethod = "mean_abs"
    save_local_shap_values: bool = False
