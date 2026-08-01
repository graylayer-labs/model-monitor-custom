from __future__ import annotations

import pickle  # ruff: ignore[suspicious-pickle-import] — trusted local artefacts in test
from pathlib import Path

import pandas as pd
import pytest
from analyser_explain import ExplainAnalyser
from mmc_base.contract import AnalyserInputs, Outcome, Severity
from sklearn.linear_model import LogisticRegression

FIXTURE = Path(__file__).parents[3] / "tests" / "fixtures" / "adult.parquet"


@pytest.fixture(scope="module")
def adult_frame() -> pd.DataFrame:
    if not FIXTURE.exists():
        pytest.skip(f"missing fixture {FIXTURE}")
    return pd.read_parquet(FIXTURE)


@pytest.fixture
def adult_model(adult_frame: pd.DataFrame, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    numeric_cols = ["age", "hours_per_week"]
    # UCI Adult in this repo drops capital_gain/education_num — synthesise a numeric feature set
    # from the columns present so top features are stable across runs.
    frame = adult_frame.copy()
    frame["age_x_hours"] = frame["age"] * frame["hours_per_week"]
    features = frame[[*numeric_cols, "age_x_hours"]].astype(float)
    labels = (frame["income"] == ">50K").astype(int)
    model = LogisticRegression(max_iter=500, random_state=0).fit(features, labels)

    tmp_dir = tmp_path_factory.mktemp("adult")
    model_path = tmp_dir / "model.pkl"
    with model_path.open("wb") as fh:
        pickle.dump(model, fh)
    features_path = tmp_dir / "features.parquet"
    features.to_parquet(features_path, index=False)
    return model_path, features_path


def test_explain_analyser_adult_end_to_end(adult_model: tuple[Path, Path]):
    model_path, features_path = adult_model
    inputs = AnalyserInputs(paths={"features": features_path})
    config = {
        "framework": "sklearn",
        "model_uri": str(model_path),
        "features_input": "features",
        "num_samples": 30,
        "background_size": 20,
        "top_k": 3,
        "agg_method": "mean_abs",
    }
    result = ExplainAnalyser().compute(inputs, config)
    assert result.outcome is Outcome.succeeded
    assert result.severity is Severity.info
    assert result.violation_count == 0

    metrics = result.analyser_metrics
    assert len(metrics) == 3
    assert all(k.startswith("shap/") for k in metrics)
    top_features = {k.removeprefix("shap/") for k in metrics}
    assert top_features & {"age", "hours_per_week", "age_x_hours"}

    payload = result.payload
    assert payload["monitor_type"] == "EXPLAINABILITY"
    assert set(payload["explanations"]["kernel_shap"]["values"]) == {"age", "hours_per_week", "age_x_hours"}
