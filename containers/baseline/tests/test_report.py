"""RED tests for AnalysisReport schema."""

from __future__ import annotations

import pytest
from model_baseline.report import (
    AnalysisReport,
    BiasFacetMetric,
    Explanations,
    GlobalShapValues,
    PreTrainingBias,
)


def _base_kwargs() -> dict:
    return {
        "package_group_name": "fraud-model",
        "baseline_version": 1,
        "monitor_type": "BIAS",
        "generated_at_utc": "2026-07-02T00:00:00Z",
        "container_image_digest": "sha256:deadbeef",
    }


def test_bias_only_report_serialises():
    report = AnalysisReport(
        pre_training_bias=PreTrainingBias(
            label="income",
            label_value_or_threshold=">50K",
            facets={"sex": [BiasFacetMetric(name="CI", value=0.1)]},
        ),
        **_base_kwargs(),
    )
    dumped = report.model_dump()
    assert dumped["explanations"] is None
    assert dumped["pre_training_bias"]["label"] == "income"


def test_explainability_only_report_serialises():
    kwargs = _base_kwargs()
    kwargs["monitor_type"] = "EXPLAINABILITY"
    report = AnalysisReport(
        explanations=Explanations(kernel_shap=GlobalShapValues(values={"age": 0.4})),
        **kwargs,
    )
    dumped = report.model_dump()
    assert dumped["pre_training_bias"] is None
    assert dumped["explanations"]["kernel_shap"]["values"]["age"] == pytest.approx(0.4)


def test_top_level_keys_match_clarify_shape():
    report = AnalysisReport(**_base_kwargs())
    dumped = report.model_dump()
    for key in ("version", "pre_training_bias", "explanations"):
        assert key in dumped


def test_round_trip():
    original = AnalysisReport(
        pre_training_bias=PreTrainingBias(
            label="income",
            label_value_or_threshold=">50K",
            facets={"sex": [BiasFacetMetric(name="CI", value=0.1)]},
        ),
        **_base_kwargs(),
    )
    round_tripped = AnalysisReport.model_validate(original.model_dump())
    assert round_tripped == original
