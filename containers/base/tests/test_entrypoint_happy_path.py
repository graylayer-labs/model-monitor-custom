from __future__ import annotations

from mmc_base.testing import NoopAnalyser, run_container_flow


def test_happy_path_exit_zero_and_writes():
    code, stubs = run_container_flow(NoopAnalyser, config={"threshold": 0.5})
    assert code == 0

    s3 = stubs["s3"]
    ddb = stubs["ddb"]
    cw = stubs["cw"]

    assert ("bucket", "out/bias/result.json") in s3.objects
    assert ("bucket", "out/bias/_provenance.json") in s3.objects
    assert ("bucket", "out/bias/failure.json") not in s3.objects

    result = s3.json_at("bucket", "out/bias/result.json")
    assert result["outcome"] == "succeeded"
    assert result["severity"] == "info"
    assert result["schema_version"] == "1.0"
    assert result["payload"] == {"noop": True}
    assert result["analyser_metrics"] == {"MetricA": 0.5}

    prov = s3.json_at("bucket", "out/bias/_provenance.json")
    assert prov["image_digest"] == "sha256:test"
    assert prov["git_sha"] == "deadbeef"

    assert len(cw.calls) == 1
    assert cw.calls[0]["Namespace"] == "mmc/analyser/v1"
    names = {m["MetricName"] for m in cw.calls[0]["MetricData"]}
    assert {"RunCount", "RunDurationSeconds", "ViolationCount", "Severity", "MetricValue"}.issubset(names)

    assert len(ddb.put_items) == 1
    item = ddb.put_items[0]["Item"]
    assert item["outcome"]["S"] == "succeeded"
    assert item["severity"]["S"] == "info"
    assert item["project"]["S"] == "example-classifier"
    assert item["result_s3_uri"]["S"] == "s3://bucket/out/bias/result.json"
    assert "failure_s3_uri" not in item
    assert "notified" not in item
