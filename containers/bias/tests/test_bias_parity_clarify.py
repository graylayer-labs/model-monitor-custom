"""Numerical parity vs a captured smclarify bias_report on UCI Adult.

Per ADR 003 we own the bias math — this test proves each pre-training
metric matches values captured from :func:`smclarify.bias.report.bias_report`
on the same dataset within ``abs_tol=1e-4``. Values are library-level
(not SageMaker service) because the service is not reproducible offline.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from analyser_bias import BiasAnalyser
from mmc_base.contract import AnalyserInputs

FIX = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
ADULT = FIX / "adult.parquet"
CLARIFY = FIX / "clarify_adult_analysis.json"


@pytest.mark.skipif(not ADULT.exists(), reason="adult.parquet fixture missing")
def test_pre_training_metrics_match_captured_clarify_values():
    canned = json.loads(CLARIFY.read_text())
    expected = canned["pre_training_metrics"]

    config = {
        "dataset_input": "dataset",
        "label_column": "income",
        "positive_label_values": [">50K"],
        "facets": [{"name": "sex", "values": ["Female"]}],
        "methods": list(expected.keys()),
    }
    output = BiasAnalyser().compute(AnalyserInputs(paths={"dataset": ADULT}), config)

    for method, want in expected.items():
        got = output.analyser_metrics[f"sex/pre/{method}"]
        assert math.isclose(got, want, abs_tol=1e-4), f"{method}: got {got}, want {want}"
