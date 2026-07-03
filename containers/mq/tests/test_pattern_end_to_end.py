"""End-to-end drive of :class:`MqAnalyser` through the base harness."""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import pandas as pd
from analyser_mq import MqAnalyser
from mmc_base.testing import run_container_flow

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
ADULT = FIXTURES / "adult.parquet"


def _predictions_bytes() -> bytes:
    """Return a parquet-serialised Adult frame with a matching prediction column."""
    df = pd.read_parquet(ADULT)[["income"]].copy()
    df["prediction"] = df["income"]
    buf = io.BytesIO()
    df.to_parquet(buf)
    return buf.getvalue()


def test_mq_analyser_end_to_end_via_base_harness():
    input_uri = "s3://bucket/in/preds.parquet"
    env_overrides = {
        "ANALYSER_TYPE": "mq",
        "OUTPUT_URI": "s3://bucket/out/mq",
        "INPUT_URIS_JSON": json.dumps({"predictions": input_uri}),
    }
    code, stubs = run_container_flow(
        MqAnalyser,
        env_overrides=env_overrides,
        config={
            "problem_type": "binary",
            "label_column": "income",
            "prediction_column": "prediction",
            "baseline_metrics": {"accuracy": 1.0},
            "degradation_thresholds": {"accuracy": 0.01},
        },
        input_bodies={"predictions": _predictions_bytes()},
    )
    assert code == 0

    s3 = stubs["s3"]
    ddb = stubs["ddb"]
    cw = stubs["cw"]

    assert ("bucket", "out/mq/result.json") in s3.objects
    assert ("bucket", "out/mq/_provenance.json") in s3.objects
    assert ("bucket", "out/mq/failure.json") not in s3.objects

    result = s3.json_at("bucket", "out/mq/result.json")
    assert result["outcome"] == "succeeded"
    assert result["severity"] == "info"
    assert result["violation_count"] == 0
    assert math.isclose(result["analyser_metrics"]["mq/accuracy"], 1.0, abs_tol=1e-9)
    assert result["payload"]["monitor_type"] == "MQ"
    assert result["payload"]["problem_type"] == "binary"

    assert len(ddb.put_items) == 1
    assert ddb.put_items[0]["Item"]["outcome"]["S"] == "succeeded"

    assert len(cw.calls) == 1
    metric_names = {m["MetricName"] for m in cw.calls[0]["MetricData"]}
    assert "RunCount" in metric_names
    assert "ViolationCount" in metric_names
