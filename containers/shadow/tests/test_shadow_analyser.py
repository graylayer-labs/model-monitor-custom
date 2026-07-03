"""Exercise :class:`ShadowAnalyser` on synthetic parquet inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from analyser_shadow import ShadowAnalyser
from mmc_base.contract import AnalyserInputs, Outcome, Severity


def _write_parquet(path: Path, df: pd.DataFrame) -> Path:
    df.to_parquet(path)
    return path


def _base_config(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "problem_type": "binary",
        "serving_variant": "AllTraffic",
        "shadow_variant": "Candidate",
        "prediction_column": "prediction",
        "probability_columns": ["p0", "p1"],
        "agreement_threshold": 0.95,
        "js_divergence_threshold": 0.1,
    }
    cfg.update(overrides)
    return cfg


def _binary_frame(preds: list[int], probs_of_1: list[float]) -> pd.DataFrame:
    p1 = np.asarray(probs_of_1, dtype=float)
    return pd.DataFrame({"prediction": preds, "p0": 1.0 - p1, "p1": p1})


def test_identical_predictions_zero_violations(tmp_path: Path) -> None:
    frame = _binary_frame([0, 1, 1, 0, 1] * 20, [0.1, 0.9, 0.8, 0.2, 0.7] * 20)
    serving = _write_parquet(tmp_path / "serving.parquet", frame)
    shadow = _write_parquet(tmp_path / "shadow.parquet", frame.copy())

    output = ShadowAnalyser().compute(
        AnalyserInputs(paths={"serving_predictions": serving, "shadow_predictions": shadow}),
        _base_config(),
    )

    assert output.outcome is Outcome.succeeded
    assert output.severity is Severity.info
    assert output.violation_count == 0
    assert output.analyser_metrics["shadow/agreement"] == pytest.approx(1.0)
    assert output.analyser_metrics["shadow/js_divergence"] == pytest.approx(0.0, abs=1e-9)


def test_small_disagreement_triggers_agreement_violation(tmp_path: Path) -> None:
    n = 100
    preds_serving = [1] * n
    preds_shadow = [1] * 90 + [0] * 10
    serving = _write_parquet(tmp_path / "serving.parquet", _binary_frame(preds_serving, [0.9] * n))
    shadow = _write_parquet(tmp_path / "shadow.parquet", _binary_frame(preds_shadow, [0.9] * 90 + [0.1] * 10))

    output = ShadowAnalyser().compute(
        AnalyserInputs(paths={"serving_predictions": serving, "shadow_predictions": shadow}),
        _base_config(agreement_threshold=0.95),
    )

    assert output.outcome is Outcome.succeeded_with_violations
    assert output.violation_count >= 1
    assert output.analyser_metrics["shadow/agreement"] == pytest.approx(0.9)
    assert output.analyser_metrics["shadow/disagreement_per_class/1"] == pytest.approx(10 / 100)


def test_probability_calibration_shift_triggers_js_violation(tmp_path: Path) -> None:
    n = 100
    preds = [0, 1] * (n // 2)
    serving_p1 = [0.05 if p == 0 else 0.95 for p in preds]
    shadow_p1 = [0.45 if p == 0 else 0.55 for p in preds]
    serving = _write_parquet(tmp_path / "serving.parquet", _binary_frame(preds, serving_p1))
    shadow = _write_parquet(tmp_path / "shadow.parquet", _binary_frame(preds, shadow_p1))

    output = ShadowAnalyser().compute(
        AnalyserInputs(paths={"serving_predictions": serving, "shadow_predictions": shadow}),
        _base_config(js_divergence_threshold=0.1),
    )

    assert output.violation_count >= 1
    assert output.analyser_metrics["shadow/agreement"] == pytest.approx(1.0)
    assert output.analyser_metrics["shadow/js_divergence"] > 0.1


def test_multiclass_per_class_disagreement(tmp_path: Path) -> None:
    classes = ["a", "b", "c"]
    n = 60
    rng = np.random.default_rng(0)
    serving_preds = [classes[i % 3] for i in range(n)]
    shadow_preds = list(serving_preds)
    for i in range(0, n, 3):
        shadow_preds[i] = "b" if serving_preds[i] == "a" else "a"

    def _probs(pred: str) -> list[float]:
        base = [0.05, 0.05, 0.05]
        base[classes.index(pred)] = 0.9
        return base

    def _frame(preds: list[str]) -> pd.DataFrame:
        prob_rows = np.asarray([_probs(p) for p in preds]) + rng.normal(0, 1e-3, (n, 3))
        prob_rows = np.clip(prob_rows, 1e-6, None)
        prob_rows /= prob_rows.sum(axis=1, keepdims=True)
        return pd.DataFrame(
            {"prediction": preds, "p_a": prob_rows[:, 0], "p_b": prob_rows[:, 1], "p_c": prob_rows[:, 2]},
        )

    serving = _write_parquet(tmp_path / "serving.parquet", _frame(serving_preds))
    shadow = _write_parquet(tmp_path / "shadow.parquet", _frame(shadow_preds))

    config = _base_config(
        problem_type="multiclass",
        probability_columns=["p_a", "p_b", "p_c"],
        agreement_threshold=0.99,
    )
    output = ShadowAnalyser().compute(
        AnalyserInputs(paths={"serving_predictions": serving, "shadow_predictions": shadow}),
        config,
    )

    assert "shadow/disagreement_per_class/a" in output.analyser_metrics
    assert "shadow/disagreement_per_class/b" in output.analyser_metrics
    assert "shadow/disagreement_per_class/c" in output.analyser_metrics
    assert output.analyser_metrics["shadow/disagreement_per_class/a"] == pytest.approx(1.0)
    assert output.analyser_metrics["shadow/disagreement_per_class/b"] == pytest.approx(0.0)
    assert output.payload["problem_type"] == "multiclass"
    assert output.payload["serving_variant"] == "AllTraffic"


def test_join_key_alignment(tmp_path: Path) -> None:
    serving = pd.DataFrame(
        {"row_id": [1, 2, 3, 4], "prediction": [0, 1, 0, 1], "p0": [0.9, 0.1, 0.8, 0.2], "p1": [0.1, 0.9, 0.2, 0.8]},
    )
    shadow = pd.DataFrame(
        {"row_id": [4, 3, 2, 1], "prediction": [1, 0, 1, 0], "p0": [0.2, 0.8, 0.1, 0.9], "p1": [0.8, 0.2, 0.9, 0.1]},
    )
    serving_path = _write_parquet(tmp_path / "serving.parquet", serving)
    shadow_path = _write_parquet(tmp_path / "shadow.parquet", shadow)

    output = ShadowAnalyser().compute(
        AnalyserInputs(paths={"serving_predictions": serving_path, "shadow_predictions": shadow_path}),
        _base_config(join_key="row_id"),
    )

    assert output.analyser_metrics["shadow/agreement"] == pytest.approx(1.0)
    assert output.violation_count == 0
