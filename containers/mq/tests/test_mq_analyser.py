"""Exercise :class:`MqAnalyser` on binary + multiclass fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from analyser_mq import MqAnalyser
from mmc_base.contract import AnalyserInputs, Outcome, Severity

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
ADULT = FIXTURES / "adult.parquet"
SYNTH3 = FIXTURES / "synthetic_3class.parquet"


def _binary_predictions(error_rate: float = 0.0, seed: int = 0) -> pd.DataFrame:
    """Return an Adult frame with a prediction column matching the label at rate ``1 - error_rate``.

    Args:
        error_rate: Fraction of rows to flip.
        seed: RNG seed for reproducibility.

    Returns:
        DataFrame with columns ``income`` and ``prediction``.
    """
    df = pd.read_parquet(ADULT)[["income"]].copy()
    rng = np.random.default_rng(seed)
    flip_mask = rng.random(len(df)) < error_rate
    labels = df["income"].to_numpy()
    other = np.where(labels == ">50K", "<=50K", ">50K")
    df["prediction"] = np.where(flip_mask, other, labels)
    positive_prob = np.where(labels == ">50K", 0.85, 0.15)
    positive_prob = np.where(flip_mask, 1.0 - positive_prob, positive_prob)
    df["p_pos"] = positive_prob
    return df


def _multiclass_predictions(error_rate: float = 0.0, seed: int = 0) -> pd.DataFrame:
    """Return the 3-class fixture with a prediction column.

    Args:
        error_rate: Fraction of rows where the prediction is shifted +1 mod 3.
        seed: RNG seed.

    Returns:
        DataFrame with columns ``label`` and ``prediction``.
    """
    df = pd.read_parquet(SYNTH3)[["label"]].copy()
    rng = np.random.default_rng(seed)
    flip_mask = rng.random(len(df)) < error_rate
    labels = df["label"].to_numpy()
    df["prediction"] = np.where(flip_mask, (labels + 1) % 3, labels)
    return df


def _write_parquet(df: pd.DataFrame, tmp_path: Path, name: str) -> Path:
    """Write ``df`` to ``tmp_path / name`` and return the path.

    Args:
        df: Frame to write.
        tmp_path: Test tmp dir.
        name: File name.

    Returns:
        Path to the parquet file.
    """
    path = tmp_path / name
    df.to_parquet(path)
    return path


@pytest.mark.skipif(not ADULT.exists(), reason="adult.parquet fixture missing")
def test_mq_analyser_binary_clean_run_matches_baseline(tmp_path: Path):
    df = _binary_predictions(error_rate=0.0)
    path = _write_parquet(df, tmp_path, "preds.parquet")

    config: dict[str, Any] = {
        "problem_type": "binary",
        "label_column": "income",
        "prediction_column": "prediction",
        "baseline_metrics": {"accuracy": 1.0, "f1_macro": 1.0},
        "degradation_thresholds": {"accuracy": 0.01, "f1_macro": 0.01},
    }
    output = MqAnalyser().compute(AnalyserInputs(paths={"predictions": path}), config)

    assert output.outcome is Outcome.succeeded
    assert output.severity is Severity.info
    assert output.violation_count == 0
    assert output.analyser_metrics["mq/accuracy"] == pytest.approx(1.0)
    assert output.analyser_metrics["mq/f1_macro"] == pytest.approx(1.0)
    assert output.payload["confusion_matrix"]["labels"] == ["<=50K", ">50K"]


@pytest.mark.skipif(not ADULT.exists(), reason="adult.parquet fixture missing")
def test_mq_analyser_binary_accuracy_violation_on_injected_errors(tmp_path: Path):
    df = _binary_predictions(error_rate=0.3, seed=42)
    path = _write_parquet(df, tmp_path, "preds.parquet")

    config: dict[str, Any] = {
        "problem_type": "binary",
        "label_column": "income",
        "prediction_column": "prediction",
        "baseline_metrics": {"accuracy": 0.95},
        "degradation_thresholds": {"accuracy": 0.05},
    }
    output = MqAnalyser().compute(AnalyserInputs(paths={"predictions": path}), config)

    assert output.outcome is Outcome.succeeded_with_violations
    assert output.severity is Severity.warn
    assert output.violation_count == 1
    v = output.payload["violations"][0]
    assert v["metric"] == "accuracy"
    assert v["current"] < 0.95 - 0.05
    assert output.payload["baseline_delta"]["accuracy"] < 0


@pytest.mark.skipif(not ADULT.exists(), reason="adult.parquet fixture missing")
def test_mq_analyser_binary_auc_when_probabilities_given(tmp_path: Path):
    df = _binary_predictions(error_rate=0.1, seed=7)
    path = _write_parquet(df, tmp_path, "preds.parquet")

    config: dict[str, Any] = {
        "problem_type": "binary",
        "label_column": "income",
        "prediction_column": "prediction",
        "probability_columns": ["p_pos"],
        "positive_label": ">50K",
    }
    output = MqAnalyser().compute(AnalyserInputs(paths={"predictions": path}), config)

    assert "mq/auc" in output.analyser_metrics
    assert 0.5 < output.analyser_metrics["mq/auc"] <= 1.0


@pytest.mark.skipif(not SYNTH3.exists(), reason="synthetic_3class.parquet fixture missing")
def test_mq_analyser_multiclass_per_class_metrics_and_confusion_matrix(tmp_path: Path):
    df = _multiclass_predictions(error_rate=0.2, seed=1)
    path = _write_parquet(df, tmp_path, "preds.parquet")

    config: dict[str, Any] = {
        "problem_type": "multiclass",
        "label_column": "label",
        "prediction_column": "prediction",
    }
    output = MqAnalyser().compute(AnalyserInputs(paths={"predictions": path}), config)

    assert output.outcome is Outcome.succeeded
    for cls in ("0", "1", "2"):
        assert f"mq/precision_per_class/{cls}" in output.analyser_metrics
        assert f"mq/recall_per_class/{cls}" in output.analyser_metrics
        assert f"mq/f1_per_class/{cls}" in output.analyser_metrics

    cm = output.payload["confusion_matrix"]
    assert cm["labels"] == ["0", "1", "2"]
    rows = cm["rows"]
    n = len(df)
    # Row sums = ground-truth class counts.
    for i, cls in enumerate((0, 1, 2)):
        expected = int((df["label"] == cls).sum())
        assert sum(rows[i]) == expected
    assert sum(sum(row) for row in rows) == n
    # Diagonal dominance for error_rate=0.2.
    for i in range(3):
        assert rows[i][i] > sum(rows[i]) - rows[i][i]


@pytest.mark.skipif(not SYNTH3.exists(), reason="synthetic_3class.parquet fixture missing")
def test_mq_analyser_alert_severity_when_many_violations(tmp_path: Path):
    df = _multiclass_predictions(error_rate=0.5, seed=3)
    path = _write_parquet(df, tmp_path, "preds.parquet")

    config: dict[str, Any] = {
        "problem_type": "multiclass",
        "label_column": "label",
        "prediction_column": "prediction",
        "baseline_metrics": {"accuracy": 0.95, "f1_macro": 0.95, "precision_macro": 0.95},
        "degradation_thresholds": {"accuracy": 0.05, "f1_macro": 0.05, "precision_macro": 0.05},
        "severity_threshold": 2,
    }
    output = MqAnalyser().compute(AnalyserInputs(paths={"predictions": path}), config)

    assert output.outcome is Outcome.succeeded_with_violations
    assert output.severity is Severity.alert
    assert output.violation_count >= 2


def test_mq_analyser_module_has_no_banned_imports():
    import analyser_mq.analyser as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "boto3" not in source
    assert "sagemaker" not in source.lower()
    assert "/opt/ml" not in source
    assert "SM_" not in source
