from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from analyser_dq import NoopDqAnalyser
from mmc_base.contract import AnalyserInputs, Outcome, Severity


def test_noop_dq_analyser_returns_canned_output():
    analyser = NoopDqAnalyser()
    output = analyser.compute(AnalyserInputs(paths={}), {})

    assert output.outcome is Outcome.succeeded
    assert output.severity is Severity.info
    assert output.violation_count == 0
    assert output.analyser_metrics == {"NoopDqSignal": 0.0}
    assert output.payload == {"note": "NoopDqAnalyser — real DQ math in Phase 5"}
    assert output.run_started_at.tzinfo is not None
    assert output.run_ended_at is not None
    assert output.run_ended_at.tzinfo is not None
    assert output.run_started_at <= output.run_ended_at


def test_noop_dq_analyser_does_no_boto3_calls():
    with patch("boto3.client") as boto_client:
        analyser = NoopDqAnalyser()
        analyser.compute(AnalyserInputs(paths={}), {"anything": True})
    boto_client.assert_not_called()


def test_noop_dq_analyser_module_has_no_banned_imports():
    import analyser_dq.analyser as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "boto3" not in source
    assert "sagemaker" not in source.lower()
    assert "/opt/ml" not in source
    assert "SM_" not in source

    for name in list(sys.modules):
        if name.startswith("analyser_dq") and "boto3" in getattr(sys.modules[name], "__dict__", {}):
            raise AssertionError(f"{name} imported boto3")
