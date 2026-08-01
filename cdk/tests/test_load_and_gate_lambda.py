"""Tests for LoadAndGate Lambda — manifest + config gating for baseline SFN."""

from __future__ import annotations

import pytest
from model_monitor_cdk.load_and_gate import LoadAndGateInput, LoadAndGateOutput, load_and_gate


@pytest.fixture
def sample_gate_input() -> LoadAndGateInput:
    """Create a sample input for load_and_gate Lambda."""
    return LoadAndGateInput(
        manifest_uri="s3://baselines/project-a/v7/input/manifest.json",
        config_uri="s3://config-prod-123456789012-eu-west-1/project-a/v1/config.json",
        project="project-a",
    )


def test_load_and_gate_accepts_valid_input(sample_gate_input):
    """LoadAndGateInput should parse valid manifest/config URIs."""
    assert sample_gate_input.project == "project-a"
    assert "manifest.json" in sample_gate_input.manifest_uri
    assert "config.json" in sample_gate_input.config_uri


def test_load_and_gate_output_structure():
    """LoadAndGateOutput should have status, message, analysers_to_run."""
    output = LoadAndGateOutput(
        status="approved",
        message="Gating passed",
        analysers_to_run=["mq", "dq"],
    )
    assert output.status == "approved"
    assert len(output.analysers_to_run) == 2


def test_load_and_gate_lambda_handler_signature():
    """load_and_gate should accept event, context and return dict."""
    import inspect

    sig = inspect.signature(load_and_gate)
    params = list(sig.parameters.keys())
    assert "event" in params
    assert "context" in params


def test_load_and_gate_requires_manifest_uri():
    """LoadAndGateInput should require manifest_uri."""
    with pytest.raises((TypeError, ValueError)):
        LoadAndGateInput(
            config_uri="s3://config/project/v1/config.json",
            project="project-a",
            # missing manifest_uri
        )


def test_load_and_gate_requires_config_uri():
    """LoadAndGateInput should require config_uri."""
    with pytest.raises((TypeError, ValueError)):
        LoadAndGateInput(
            manifest_uri="s3://baselines/project/v1/input/manifest.json",
            project="project-a",
            # missing config_uri
        )


def test_load_and_gate_requires_project():
    """LoadAndGateInput should require project name."""
    with pytest.raises((TypeError, ValueError)):
        LoadAndGateInput(
            manifest_uri="s3://baselines/project/v1/input/manifest.json",
            config_uri="s3://config/project/v1/config.json",
            # missing project
        )
