"""End-to-end drive of :class:`DqAnalyser` through the base container flow."""

from __future__ import annotations

import io
import json

import pandas as pd
from analyser_dq import DqAnalyser
from mmc_base.testing import run_container_flow


def _numeric_parquet(mean: float, size: int = 500, seed: int = 0) -> bytes:
    """Return a small numeric+categorical parquet with a controllable mean.

    Args:
        mean: Mean of the numeric ``x`` column.
        size: Row count.
        seed: RNG seed.

    Returns:
        Parquet bytes.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        {
            "x": rng.normal(loc=mean, scale=1.0, size=size),
            "group": rng.choice(["a", "b", "c"], size=size, p=[0.5, 0.3, 0.2]),
        },
    )
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


def test_dq_analyser_end_to_end_via_base_harness() -> None:
    config = {
        "numeric_columns": ["x"],
        "categorical_columns": ["group"],
        "completeness_threshold": 0.99,
        "ks_p_value_threshold": 0.05,
        "psi_threshold": 0.2,
        "severity_threshold": 3,
    }
    env_overrides = {
        "ANALYSER_TYPE": "dq",
        "OUTPUT_URI": "s3://bucket/out/dq",
        "INPUT_URIS_JSON": json.dumps(
            {
                "current": "s3://bucket/in/current.parquet",
                "baseline": "s3://bucket/in/baseline.parquet",
            },
        ),
    }

    code, stubs = run_container_flow(
        DqAnalyser,
        env_overrides=env_overrides,
        config=config,
        input_bodies={
            "current": _numeric_parquet(mean=5.0, seed=1),
            "baseline": _numeric_parquet(mean=0.0, seed=2),
        },
    )
    assert code == 0

    s3 = stubs["s3"]
    ddb = stubs["ddb"]
    cw = stubs["cw"]

    assert ("bucket", "out/dq/result.json") in s3.objects
    assert ("bucket", "out/dq/_provenance.json") in s3.objects
    assert ("bucket", "out/dq/failure.json") not in s3.objects

    result = s3.json_at("bucket", "out/dq/result.json")
    assert result["outcome"] == "succeeded_with_violations"
    assert result["schema_version"] == "1.0"
    assert result["violation_count"] >= 1
    assert "dq/x/ks_stat" in result["analyser_metrics"]
    assert "dq/x/ks_p" in result["analyser_metrics"]
    assert "dq/group/psi" in result["analyser_metrics"]
    assert result["payload"]["monitor_type"] == "DATA_QUALITY"

    assert ddb.put_items[0]["Item"]["outcome"]["S"] == "succeeded_with_violations"

    ks_p_metrics = [
        m
        for m in cw.calls[0]["MetricData"]
        if m["MetricName"] == "MetricValue"
        and any(d["Name"] == "MetricName" and d["Value"] == "dq/x/ks_p" for d in m["Dimensions"])
    ]
    assert len(ks_p_metrics) == 1
    assert ks_p_metrics[0]["Value"] < 0.05
