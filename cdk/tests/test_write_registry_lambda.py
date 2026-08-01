"""Tests for WriteRegistry Lambda — record baseline approval in DynamoDB."""

from __future__ import annotations

import pytest
from model_monitor_cdk.write_registry import (
    WriteRegistryInput,
    WriteRegistryOutput,
    write_registry,
)


@pytest.fixture
def sample_write_registry_input() -> WriteRegistryInput:
    """Create a sample input for write_registry Lambda."""
    return WriteRegistryInput(
        project="project-a",
        model_version="7",
        status="approved",
        baseline_prefix="s3://baselines/project-a/v7/output/",
        analysers={"mq": "ok", "dq": "ok", "shadow": "ok"},
        manifest_uri="s3://baselines/project-a/v7/input/manifest.json",
        sfn_execution_arn="arn:aws:states:eu-west-1:123456789012:execution:baseline-sfn:abc123",
    )


def test_write_registry_accepts_valid_input(sample_write_registry_input):
    """WriteRegistryInput should parse valid baseline record."""
    assert sample_write_registry_input.project == "project-a"
    assert sample_write_registry_input.status == "approved"
    assert len(sample_write_registry_input.analysers) == 3


def test_write_registry_output_structure():
    """WriteRegistryOutput should have project, model_version, written."""
    output = WriteRegistryOutput(
        project="project-a",
        model_version="7",
        written=True,
    )
    assert output.project == "project-a"
    assert output.model_version == "7"
    assert output.written is True


def test_write_registry_lambda_handler_signature():
    """write_registry should accept event, context and return dict."""
    import inspect

    sig = inspect.signature(write_registry)
    params = list(sig.parameters.keys())
    assert "event" in params
    assert "context" in params


def test_write_registry_requires_project():
    """WriteRegistryInput should require project."""
    with pytest.raises((TypeError, ValueError)):
        WriteRegistryInput(
            model_version="7",
            status="approved",
            baseline_prefix="s3://baselines/project-a/v7/output/",
            analysers={"mq": "ok"},
            manifest_uri="s3://baselines/project-a/v7/input/manifest.json",
            sfn_execution_arn="arn:aws:states:eu-west-1:123456789012:execution:sfn:abc",
            # missing project
        )


def test_write_registry_requires_status():
    """WriteRegistryInput should require status (approved/rejected)."""
    with pytest.raises((TypeError, ValueError)):
        WriteRegistryInput(
            project="project-a",
            model_version="7",
            baseline_prefix="s3://baselines/project-a/v7/output/",
            analysers={"mq": "ok"},
            manifest_uri="s3://baselines/project-a/v7/input/manifest.json",
            sfn_execution_arn="arn:aws:states:eu-west-1:123456789012:execution:sfn:abc",
            # missing status
        )


def test_write_registry_requires_analysers():
    """WriteRegistryInput should require analysers dict."""
    with pytest.raises((TypeError, ValueError)):
        WriteRegistryInput(
            project="project-a",
            model_version="7",
            status="approved",
            baseline_prefix="s3://baselines/project-a/v7/output/",
            manifest_uri="s3://baselines/project-a/v7/input/manifest.json",
            sfn_execution_arn="arn:aws:states:eu-west-1:123456789012:execution:sfn:abc",
            # missing analysers
        )
