from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mmc_base.contract import AnalyserInputs, AnalyserOutput, Outcome
from mmc_base.testing import run_container_flow


class Boom:
    def compute(self, inputs: AnalyserInputs, config: dict[str, Any]) -> AnalyserOutput:
        raise KeyError("label")


class AnalyserSetsFailedUnhandled:
    def compute(self, inputs: AnalyserInputs, config: dict[str, Any]) -> AnalyserOutput:
        return AnalyserOutput(outcome=Outcome.failed_unhandled, run_started_at=datetime.now(UTC))


def test_analyser_exception_writes_failure_sidecar_and_ddb_row():
    code, stubs = run_container_flow(Boom)
    assert code == 1

    s3 = stubs["s3"]
    ddb = stubs["ddb"]

    assert ("bucket", "out/bias/failure.json") in s3.objects
    sidecar = s3.json_at("bucket", "out/bias/failure.json")
    assert sidecar["outcome"] == "failed_unhandled"
    assert sidecar["exception_class"] == "KeyError"
    assert "label" in sidecar["message"]
    assert "Traceback" in sidecar["traceback"]

    assert len(ddb.put_items) == 1
    item = ddb.put_items[0]["Item"]
    assert item["outcome"]["S"] == "failed_unhandled"
    assert item["severity"]["S"] == "alert"
    assert item["failure_s3_uri"]["S"] == "s3://bucket/out/bias/failure.json"


def test_analyser_returning_failed_unhandled_is_rejected():
    code, stubs = run_container_flow(AnalyserSetsFailedUnhandled)
    assert code == 1
    sidecar = stubs["s3"].json_at("bucket", "out/bias/failure.json")
    assert sidecar["outcome"] == "failed_unhandled"
    assert sidecar["exception_class"] == "ValueError"
