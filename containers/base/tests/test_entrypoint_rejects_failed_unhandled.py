from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mmc_base.contract import AnalyserInputs, AnalyserOutput, Outcome
from mmc_base.testing import run_container_flow


class BadAnalyser:
    def compute(self, inputs: AnalyserInputs, config: dict[str, Any]) -> AnalyserOutput:
        return AnalyserOutput(outcome=Outcome.failed_unhandled, run_started_at=datetime.now(UTC))


def test_analyser_cannot_set_failed_unhandled():
    code, stubs = run_container_flow(BadAnalyser)
    assert code == 1
    sidecar = stubs["s3"].json_at("bucket", "out/bias/failure.json")
    assert sidecar["outcome"] == "failed_unhandled"
    ddb_item = stubs["ddb"].put_items[0]["Item"]
    assert ddb_item["outcome"]["S"] == "failed_unhandled"
    assert ddb_item["severity"]["S"] == "alert"
