from __future__ import annotations

import json
import pickle  # noqa: S403 — trusted local artefacts in test
from pathlib import Path

import pandas as pd
from analyser_explain import ExplainAnalyser
from mmc_base.testing import run_container_flow
from sklearn.datasets import make_classification
from sklearn.linear_model import LogisticRegression


def _train_and_persist(tmp: Path) -> tuple[Path, Path]:
    x, y = make_classification(n_samples=150, n_features=4, n_classes=2, random_state=0)
    headers = [f"f{i}" for i in range(4)]
    frame = pd.DataFrame(x, columns=headers)
    model = LogisticRegression(max_iter=300).fit(frame, y)
    model_path = tmp / "model.pkl"
    with model_path.open("wb") as fh:
        pickle.dump(model, fh)
    features_path = tmp / "features.parquet"
    frame.to_parquet(features_path, index=False)
    return model_path, features_path


def test_explain_analyser_end_to_end_via_base_harness(tmp_path: Path):
    model_path, features_path = _train_and_persist(tmp_path)
    config = {
        "framework": "sklearn",
        "model_uri": str(model_path),
        "features_input": "snapshot",
        "num_samples": 20,
        "background_size": 10,
        "top_k": 2,
    }
    input_bodies = {"snapshot": features_path.read_bytes()}
    env_overrides = {
        "ANALYSER_TYPE": "explain",
        "INPUT_URIS_JSON": json.dumps({"snapshot": "s3://bucket/in/features.parquet"}),
        "OUTPUT_URI": "s3://bucket/out/explain",
    }
    code, stubs = run_container_flow(
        ExplainAnalyser,
        env_overrides=env_overrides,
        config=config,
        input_bodies=input_bodies,
    )
    assert code == 0

    s3 = stubs["s3"]
    assert ("bucket", "out/explain/result.json") in s3.objects
    assert ("bucket", "out/explain/failure.json") not in s3.objects

    result = s3.json_at("bucket", "out/explain/result.json")
    assert result["outcome"] == "succeeded"
    assert result["severity"] == "info"
    assert result["violation_count"] == 0
    assert all(k.startswith("shap/") for k in result["analyser_metrics"])
    assert len(result["analyser_metrics"]) == 2
    assert result["payload"]["monitor_type"] == "EXPLAINABILITY"

    cw_calls = stubs["cw"].calls
    assert len(cw_calls) == 1
    shap_metrics = [
        m
        for m in cw_calls[0]["MetricData"]
        if m["MetricName"] == "MetricValue"
        and any(d["Name"] == "MetricName" and d["Value"].startswith("shap/") for d in m["Dimensions"])
    ]
    assert len(shap_metrics) == 2
