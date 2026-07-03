"""End-to-end harness run of :class:`ShadowAnalyser` through the base entrypoint."""

from __future__ import annotations

import io
import json

import numpy as np
import pandas as pd
import pytest
from analyser_shadow import ShadowAnalyser
from mmc_base.testing import run_container_flow


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf)
    return buf.getvalue()


def test_shadow_analyser_end_to_end_via_base_harness() -> None:  # noqa: PLR0914 — harness plumbing needs many locals
    n = 40
    rng = np.random.default_rng(1)
    preds = rng.integers(0, 2, size=n).tolist()
    p1 = np.where(np.array(preds) == 1, 0.9, 0.1)
    serving_df = pd.DataFrame({"prediction": preds, "p0": 1.0 - p1, "p1": p1})
    shadow_df = serving_df.copy()

    input_uris = {
        "serving_predictions": "s3://bucket/in/serving.parquet",
        "shadow_predictions": "s3://bucket/in/shadow.parquet",
    }
    input_bodies = {
        "serving_predictions": _parquet_bytes(serving_df),
        "shadow_predictions": _parquet_bytes(shadow_df),
    }

    config = {
        "problem_type": "binary",
        "serving_variant": "AllTraffic",
        "shadow_variant": "Candidate",
        "probability_columns": ["p0", "p1"],
    }

    code, stubs = run_container_flow(
        ShadowAnalyser,
        env_overrides={
            "ANALYSER_TYPE": "shadow",
            "OUTPUT_URI": "s3://bucket/out/shadow",
            "INPUT_URIS_JSON": json.dumps(input_uris),
        },
        config=config,
        input_bodies=input_bodies,
    )
    assert code == 0

    s3 = stubs["s3"]
    ddb = stubs["ddb"]
    cw = stubs["cw"]

    assert ("bucket", "out/shadow/result.json") in s3.objects
    assert ("bucket", "out/shadow/_provenance.json") in s3.objects
    assert ("bucket", "out/shadow/failure.json") not in s3.objects

    result = s3.json_at("bucket", "out/shadow/result.json")
    assert result["outcome"] == "succeeded"
    assert result["severity"] == "info"
    assert result["schema_version"] == "1.0"
    assert result["violation_count"] == 0
    assert result["analyser_metrics"]["shadow/agreement"] == pytest.approx(1.0)
    assert result["payload"]["monitor_type"] == "SHADOW"
    assert result["payload"]["serving_variant"] == "AllTraffic"
    assert result["payload"]["shadow_variant"] == "Candidate"

    assert len(ddb.put_items) == 1
    item = ddb.put_items[0]["Item"]
    assert item["outcome"]["S"] == "succeeded"
    assert item["analyser_type"]["S"] == "shadow"
    assert "failure_s3_uri" not in item

    assert len(cw.calls) == 1
    metric_names = {
        d["Value"]
        for m in cw.calls[0]["MetricData"]
        if m["MetricName"] == "MetricValue"
        for d in m["Dimensions"]
        if d["Name"] == "MetricName"
    }
    assert "shadow/agreement" in metric_names
    assert "shadow/js_divergence" in metric_names
