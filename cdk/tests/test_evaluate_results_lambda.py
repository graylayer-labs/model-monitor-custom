"""Tests for EvaluateResults Lambda — check required analysers succeeded."""

from __future__ import annotations

import pytest

from model_monitor_cdk.evaluate_results import (
    EvaluateResultsInput,
    EvaluateResultsOutput,
    evaluate_results,
)


@pytest.fixture
def sample_evaluate_input() -> EvaluateResultsInput:
    """Create a sample input for evaluate_results Lambda."""
    return EvaluateResultsInput(
        gate_output={
            "status": "approved",
            "analysers_to_run": ["mq", "dq", "shadow"],
        },
        analyser_results={
            "mq": {"analyser": "mq", "outcome": "ok"},
            "dq": {"analyser": "dq", "outcome": "ok"},
            "shadow": {"analyser": "shadow", "outcome": "ok"},
        },
        required_analysers=["mq", "dq"],
    )


def test_evaluate_results_accepts_valid_input(sample_evaluate_input):
    """EvaluateResultsInput should parse gate output + analyser results."""
    assert sample_evaluate_input.required_analysers == ["mq", "dq"]
    assert len(sample_evaluate_input.analyser_results) == 3


def test_evaluate_results_output_structure():
    """EvaluateResultsOutput should have status, message, all_required_ok."""
    output = EvaluateResultsOutput(
        status="approved",
        message="All required analysers passed",
        all_required_ok=True,
    )
    assert output.status == "approved"
    assert output.all_required_ok is True


def test_evaluate_results_lambda_handler_signature():
    """evaluate_results should accept event, context and return dict."""
    import inspect

    sig = inspect.signature(evaluate_results)
    params = list(sig.parameters.keys())
    assert "event" in params
    assert "context" in params


def test_evaluate_results_requires_gate_output():
    """EvaluateResultsInput should require gate_output."""
    with pytest.raises((TypeError, ValueError)):
        EvaluateResultsInput(
            analyser_results={"mq": {"outcome": "ok"}},
            required_analysers=["mq"],
            # missing gate_output
        )


def test_evaluate_results_requires_analyser_results():
    """EvaluateResultsInput should require analyser_results."""
    with pytest.raises((TypeError, ValueError)):
        EvaluateResultsInput(
            gate_output={"status": "approved", "analysers_to_run": ["mq"]},
            required_analysers=["mq"],
            # missing analyser_results
        )


def test_evaluate_results_requires_required_analysers():
    """EvaluateResultsInput should require required_analysers list."""
    with pytest.raises((TypeError, ValueError)):
        EvaluateResultsInput(
            gate_output={"status": "approved", "analysers_to_run": ["mq"]},
            analyser_results={"mq": {"outcome": "ok"}},
            # missing required_analysers
        )
