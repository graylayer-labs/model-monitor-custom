"""RED tests for BiasSpec + ExplainSpec."""

from __future__ import annotations

import pytest
from model_baseline.specs import BiasSpec, ExplainSpec, Facet
from pydantic import ValidationError


class TestBiasSpec:
    def test_minimal_valid(self):
        spec = BiasSpec(
            label_column="income",
            positive_label_values=[">50K"],
            facets=[Facet(name="sex", values=["Female"])],
        )
        assert spec.methods == ["CI", "DPL", "KL", "JS"]

    def test_empty_facets_allowed(self):
        spec = BiasSpec(
            label_column="income",
            positive_label_values=[">50K"],
            facets=[],
        )
        assert spec.facets == []

    def test_unknown_method_rejected(self):
        with pytest.raises(ValidationError):
            BiasSpec(
                label_column="y",
                positive_label_values=[1],
                facets=[],
                methods=["NOPE"],  # ty: ignore[invalid-argument-type]
            )

    def test_duplicate_facet_names_rejected(self):
        with pytest.raises(ValidationError):
            BiasSpec(
                label_column="y",
                positive_label_values=[1],
                facets=[
                    Facet(name="sex", values=["F"]),
                    Facet(name="sex", values=["M"]),
                ],
            )


class TestExplainSpec:
    def test_defaults(self):
        spec = ExplainSpec()
        assert spec.num_samples == 100
        assert spec.background_size == 50
        assert spec.agg_method == "mean_abs"
        assert spec.save_local_shap_values is False

    def test_num_samples_upper_bound(self):
        with pytest.raises(ValidationError):
            ExplainSpec(num_samples=10_001)

    def test_unknown_agg_method_rejected(self):
        with pytest.raises(ValidationError):
            ExplainSpec(agg_method="max")  # ty: ignore[invalid-argument-type]
