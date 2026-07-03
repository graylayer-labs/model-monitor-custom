from __future__ import annotations

import math

from analyser_shadow import NoopShadowAnalyser
from mmc_base.testing import run_container_flow


def test_noop_shadow_analyser_end_to_end_via_base_harness():
    code, stubs = run_container_flow(
        NoopShadowAnalyser,
        env_overrides={
            "ANALYSER_TYPE": "shadow",
            "OUTPUT_URI": "s3://bucket/out/shadow",
        },
        config={"threshold": 0.5},
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
    assert result["analyser_metrics"] == {"NoopDqSignal": 0.0}
    assert result["payload"] == {"note": "NoopShadowAnalyser — real Shadow math in Phase 5"}

    assert len(ddb.put_items) == 1
    item = ddb.put_items[0]["Item"]
    assert item["outcome"]["S"] == "succeeded"
    assert item["analyser_type"]["S"] == "shadow"
    assert "failure_s3_uri" not in item

    assert len(cw.calls) == 1
    metrics = {m["MetricName"]: m for m in cw.calls[0]["MetricData"]}
    assert metrics["RunCount"]["Value"] == 1
    assert metrics["ViolationCount"]["Value"] == 0
    assert metrics["Severity"]["Value"] == 0
    noop_metrics = [
        m
        for m in cw.calls[0]["MetricData"]
        if m["MetricName"] == "MetricValue"
        and any(d["Name"] == "MetricName" and d["Value"] == "NoopDqSignal" for d in m["Dimensions"])
    ]
    assert len(noop_metrics) == 1
    assert math.isclose(noop_metrics[0]["Value"], 0.0, abs_tol=1e-9)
