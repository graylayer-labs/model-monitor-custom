from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import xgboost as xgb
from analyser_explain import ExplainAnalyser
from mmc_base.contract import AnalyserInputs, Outcome

FIXTURE = Path(__file__).parents[3] / "tests" / "fixtures" / "synthetic_3class.parquet"


@pytest.fixture
def xgb_multiclass_model(tmp_path: Path) -> tuple[Path, Path]:
    if not FIXTURE.exists():
        pytest.skip(f"missing fixture {FIXTURE}")
    frame = pd.read_parquet(FIXTURE)
    labels = frame["label"].astype(int)
    features = frame.drop(columns=["label"])
    headers = list(features.columns)
    dmat = xgb.DMatrix(features, label=labels, feature_names=headers)
    booster = xgb.train(
        {"objective": "multi:softprob", "num_class": 3, "max_depth": 3, "seed": 0},
        dmat,
        num_boost_round=20,
    )
    model_path = tmp_path / "model.json"
    booster.save_model(str(model_path))
    features_path = tmp_path / "features.parquet"
    features.to_parquet(features_path, index=False)
    return model_path, features_path


def test_explain_analyser_multiclass_xgb(xgb_multiclass_model: tuple[Path, Path]):
    model_path, features_path = xgb_multiclass_model
    inputs = AnalyserInputs(paths={"features": features_path})
    config = {
        "framework": "xgboost",
        "model_uri": str(model_path),
        "features_input": "features",
        "num_samples": 50,
        "background_size": 20,
        "top_k": 5,
    }
    result = ExplainAnalyser().compute(inputs, config)
    assert result.outcome is Outcome.succeeded
    assert len(result.analyser_metrics) == 5
    for name in result.analyser_metrics:
        assert name.startswith("shap/")

    values = result.payload["explanations"]["kernel_shap"]["values"]
    assert len(values) == 10
    assert all(v >= 0 for v in values.values())
    assert max(values.values()) > 0
