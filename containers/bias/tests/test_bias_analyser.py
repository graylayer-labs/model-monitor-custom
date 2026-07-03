"""Exercise :class:`BiasAnalyser` end-to-end on UCI Adult."""

from __future__ import annotations

from pathlib import Path

import pytest
from analyser_bias import BiasAnalyser
from mmc_base.contract import AnalyserInputs, Outcome, Severity

ADULT = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "adult.parquet"


@pytest.fixture
def bias_config() -> dict[str, object]:
    return {
        "dataset_input": "dataset",
        "label_column": "income",
        "positive_label_values": [">50K"],
        "facets": [{"name": "sex", "values": ["Female"]}],
        "methods": ["CI", "DPL", "KL", "JS"],
        "thresholds": {"DPL": 0.1},
    }


@pytest.mark.skipif(not ADULT.exists(), reason="adult.parquet fixture missing")
def test_bias_analyser_flags_adult_income_bias(bias_config: dict[str, object]):
    output = BiasAnalyser().compute(AnalyserInputs(paths={"dataset": ADULT}), bias_config)

    assert output.outcome is Outcome.succeeded_with_violations
    assert output.severity is Severity.warn
    assert output.violation_count >= 1

    expected_keys = {f"sex/pre/{m}" for m in ("CI", "DPL", "KL", "JS")}
    assert expected_keys.issubset(output.analyser_metrics.keys())
    assert output.analyser_metrics["sex/pre/DPL"] > 0.1


@pytest.mark.skipif(not ADULT.exists(), reason="adult.parquet fixture missing")
def test_bias_analyser_payload_shape(bias_config: dict[str, object]):
    output = BiasAnalyser().compute(AnalyserInputs(paths={"dataset": ADULT}), bias_config)
    payload = output.payload

    assert payload["monitor_type"] == "BIAS"
    assert payload["label"] == "income"
    assert payload["label_value_or_threshold"] == ">50K"
    assert "pre_training_bias" in payload
    facets = payload["pre_training_bias"]["facets"]
    assert "sex" in facets
    names = {m["name"] for m in facets["sex"]}
    assert {"CI", "DPL", "KL", "JS"}.issubset(names)


@pytest.mark.skipif(not ADULT.exists(), reason="adult.parquet fixture missing")
def test_bias_analyser_clean_run_when_no_thresholds_breached():
    config = {
        "dataset_input": "dataset",
        "label_column": "income",
        "positive_label_values": [">50K"],
        "facets": [{"name": "sex", "values": ["Female"]}],
        "methods": ["CI", "DPL"],
        "thresholds": {"CI": 1.0, "DPL": 1.0},
    }
    output = BiasAnalyser().compute(AnalyserInputs(paths={"dataset": ADULT}), config)

    assert output.outcome is Outcome.succeeded
    assert output.severity is Severity.info
    assert output.violation_count == 0


@pytest.mark.skipif(not ADULT.exists(), reason="adult.parquet fixture missing")
def test_bias_analyser_alert_severity_on_alert_threshold():
    config = {
        "dataset_input": "dataset",
        "label_column": "income",
        "positive_label_values": [">50K"],
        "facets": [{"name": "sex", "values": ["Female"]}],
        "methods": ["DPL"],
        "thresholds": {"DPL": 0.05},
        "severity_alert_thresholds": {"DPL": 0.1},
    }
    output = BiasAnalyser().compute(AnalyserInputs(paths={"dataset": ADULT}), config)

    assert output.severity is Severity.alert
