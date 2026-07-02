from __future__ import annotations

from mmc_base.testing import NoopAnalyser, run_container_flow


def test_result_and_provenance_shapes():
    _code, stubs = run_container_flow(NoopAnalyser)
    s3 = stubs["s3"]
    result = s3.json_at("bucket", "out/bias/result.json")
    prov = s3.json_at("bucket", "out/bias/_provenance.json")

    required_result = {
        "schema_version",
        "outcome",
        "severity",
        "violation_count",
        "analyser_metrics",
        "run_started_at",
        "run_ended_at",
        "payload",
    }
    required_prov = {"schema_version", "image_digest", "git_sha", "env_snapshot", "run_started_at", "run_ended_at"}
    assert required_result <= set(result)
    assert required_prov <= set(prov)
