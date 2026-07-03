"""Unit tests for :class:`DqAnalyser`.

Uses ``tests/fixtures/adult.parquet`` as the baseline and synthesises
drifted copies in-test so each violation type is exercised in isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from analyser_dq import DqAnalyser
from mmc_base.contract import AnalyserInputs, AnalyserOutput, Outcome, Severity

REPO_ROOT = Path(__file__).resolve().parents[3]
ADULT_PARQUET = REPO_ROOT / "tests" / "fixtures" / "adult.parquet"

_NUMERIC = ["age", "hours_per_week"]
_CATEGORICAL = ["workclass", "sex", "education"]


def _write_parquet(tmp_path: Path, name: str, df: pd.DataFrame) -> Path:
    """Serialise ``df`` to a parquet file for the analyser to read.

    Args:
        tmp_path: pytest-provided temp dir.
        name: Filename stem.
        df: Frame to persist.

    Returns:
        Path to the written parquet.
    """
    path = tmp_path / f"{name}.parquet"
    df.to_parquet(path, index=False)
    return path


@pytest.fixture(name="baseline_df")
def _baseline_df() -> pd.DataFrame:
    """Return the adult baseline frame."""
    return pd.read_parquet(ADULT_PARQUET)


def _config(**overrides: object) -> dict[str, object]:
    """Return a default DqSpec-compatible config with overrides.

    Args:
        **overrides: Values that replace defaults.

    Returns:
        Config dict.
    """
    cfg: dict[str, object] = {
        "numeric_columns": _NUMERIC,
        "categorical_columns": _CATEGORICAL,
        "completeness_threshold": 0.99,
        "ks_p_value_threshold": 0.05,
        "psi_threshold": 0.2,
        "severity_threshold": 3,
    }
    cfg.update(overrides)
    return cfg


def _run(tmp_path: Path, current: pd.DataFrame, baseline: pd.DataFrame, **cfg: object) -> AnalyserOutput:
    """Materialise inputs and run the analyser.

    Args:
        tmp_path: pytest-provided temp dir.
        current: Current-window frame.
        baseline: Baseline snapshot frame.
        **cfg: Config overrides.

    Returns:
        The analyser output.
    """
    cur_path = _write_parquet(tmp_path, "current", current)
    base_path = _write_parquet(tmp_path, "baseline", baseline)
    inputs = AnalyserInputs(paths={"current": cur_path, "baseline": base_path})
    return DqAnalyser().compute(inputs, _config(**cfg))


def test_baseline_equals_current_no_violations(tmp_path: Path, baseline_df: pd.DataFrame) -> None:
    output = _run(tmp_path, baseline_df, baseline_df)
    assert output.outcome is Outcome.succeeded
    assert output.severity is Severity.info
    assert output.violation_count == 0
    for col in _NUMERIC:
        assert f"dq/{col}/ks_stat" in output.analyser_metrics
        assert f"dq/{col}/ks_p" in output.analyser_metrics
    for col in _CATEGORICAL:
        assert f"dq/{col}/psi" in output.analyser_metrics


def test_numeric_drift_triggers_ks_violation(tmp_path: Path, baseline_df: pd.DataFrame) -> None:
    current = baseline_df.copy()
    current["age"] += 30
    output = _run(tmp_path, current, baseline_df)
    assert output.outcome is Outcome.succeeded_with_violations
    types = {v["type"] for v in output.payload["violations"]}
    assert "numeric_drift" in types
    assert output.analyser_metrics["dq/age/ks_p"] < 0.05


def test_categorical_drift_triggers_psi_violation(tmp_path: Path, baseline_df: pd.DataFrame) -> None:
    current = baseline_df.copy()
    current["sex"] = "Male"
    output = _run(tmp_path, current, baseline_df)
    types = {v["type"] for v in output.payload["violations"]}
    assert "categorical_drift" in types
    assert output.analyser_metrics["dq/sex/psi"] > 0.2


def test_null_injection_triggers_completeness_violation(tmp_path: Path, baseline_df: pd.DataFrame) -> None:
    current = baseline_df.copy()
    rng = np.random.default_rng(42)
    mask = rng.random(len(current)) < 0.2
    current.loc[mask, "workclass"] = None
    output = _run(tmp_path, current, baseline_df)
    completeness_violations = [v for v in output.payload["violations"] if v["type"] == "completeness"]
    assert any(v["column"] == "workclass" for v in completeness_violations)
    assert output.analyser_metrics["dq/workclass/completeness"] < 0.99


def test_missing_column_triggers_schema_violation(tmp_path: Path, baseline_df: pd.DataFrame) -> None:
    current = baseline_df.drop(columns=["education"])
    output = _run(tmp_path, current, baseline_df)
    types = {v["type"] for v in output.payload["violations"]}
    assert "schema_missing" in types
    missing = [v for v in output.payload["violations"] if v["type"] == "schema_missing"]
    assert any(v["column"] == "education" for v in missing)


def test_severity_escalates_to_alert(tmp_path: Path, baseline_df: pd.DataFrame) -> None:
    current = baseline_df.copy()
    current["age"] += 50
    current["hours_per_week"] += 30
    current["sex"] = "Male"
    output = _run(tmp_path, current, baseline_df, severity_threshold=2)
    assert output.severity is Severity.alert


def test_analyser_does_no_boto3_calls(tmp_path: Path, baseline_df: pd.DataFrame) -> None:
    with patch("boto3.client") as boto_client:
        _run(tmp_path, baseline_df, baseline_df)
    boto_client.assert_not_called()


def test_module_has_no_banned_imports() -> None:
    import analyser_dq.analyser as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "boto3" not in source
    assert "sagemaker" not in source.lower()
    assert "/opt/ml" not in source
    assert "SM_" not in source

    for name in list(sys.modules):
        if name.startswith("analyser_dq") and "boto3" in getattr(sys.modules[name], "__dict__", {}):
            raise AssertionError(f"{name} imported boto3")
