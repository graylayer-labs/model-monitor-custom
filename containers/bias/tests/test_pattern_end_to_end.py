"""End-to-end drive of :class:`BiasAnalyser` through the base container flow."""

from __future__ import annotations

import io
import json

import pandas as pd
from analyser_bias import BiasAnalyser
from mmc_base.testing import run_container_flow


def _synthetic_parquet_bytes() -> bytes:
    """Return a small parquet dataset with a strong bias signal."""
    rows = [{"sex": "Female", "income": "<=50K"} for _ in range(50)]
    rows.extend({"sex": "Male", "income": ">50K"} for _ in range(50))
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


def test_bias_analyser_end_to_end_via_base_harness():
    config = {
        "dataset_input": "dataset",
        "label_column": "income",
        "positive_label_values": [">50K"],
        "facets": [{"name": "sex", "values": ["Female"]}],
        "methods": ["CI", "DPL", "KL", "JS"],
        "thresholds": {"DPL": 0.1},
    }
    env_overrides = {"INPUT_URIS_JSON": json.dumps({"dataset": "s3://bucket/in/dataset.parquet"})}

    code, stubs = run_container_flow(
        BiasAnalyser,
        env_overrides=env_overrides,
        config=config,
        input_bodies={"dataset": _synthetic_parquet_bytes()},
    )
    assert code == 0

    s3 = stubs["s3"]
    ddb = stubs["ddb"]
    cw = stubs["cw"]

    assert ("bucket", "out/bias/result.json") in s3.objects
    assert ("bucket", "out/bias/failure.json") not in s3.objects

    result = s3.json_at("bucket", "out/bias/result.json")
    assert result["outcome"] == "succeeded_with_violations"
    assert result["severity"] == "warn"
    assert result["violation_count"] >= 1
    assert "sex/pre/DPL" in result["analyser_metrics"]
    assert result["payload"]["monitor_type"] == "BIAS"

    assert ddb.put_items[0]["Item"]["outcome"]["S"] == "succeeded_with_violations"

    dpl_metrics = [
        m
        for m in cw.calls[0]["MetricData"]
        if m["MetricName"] == "MetricValue"
        and any(d["Name"] == "MetricName" and d["Value"] == "sex/pre/DPL" for d in m["Dimensions"])
    ]
    assert len(dpl_metrics) == 1
    assert dpl_metrics[0]["Value"] > 0.5
