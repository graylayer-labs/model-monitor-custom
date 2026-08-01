"""Tests for Activation Lambda — read baseline registry and allow monitoring if approved."""

from __future__ import annotations

from datetime import UTC, datetime

import boto3
import pytest
from model_monitor_cdk.activation import (
    ActivationInput,
    ActivationOutput,
    activation,
)
from moto import mock_aws


@pytest.fixture
def sample_activation_input() -> ActivationInput:
    """Create a sample input for activation Lambda."""
    return ActivationInput(
        project="project-a",
        model_version="7",
    )


def test_activation_input_requires_project():
    """ActivationInput should require project."""
    with pytest.raises((TypeError, ValueError)):
        ActivationInput(
            model_version="7",
            # missing project
        )


def test_activation_input_requires_model_version():
    """ActivationInput should require model_version."""
    with pytest.raises((TypeError, ValueError)):
        ActivationInput(
            project="project-a",
            # missing model_version
        )


def test_activation_input_accepts_valid_input(sample_activation_input):
    """ActivationInput should parse valid query."""
    assert sample_activation_input.project == "project-a"
    assert sample_activation_input.model_version == "7"


def test_activation_output_structure():
    """ActivationOutput should have required fields."""
    output = ActivationOutput(
        activate=True,
        baseline_prefix="s3://baselines/project-a/v7/output/",
        analysers={"mq": "ok", "dq": "ok"},
        reason=None,
    )
    assert output.activate is True
    assert output.baseline_prefix == "s3://baselines/project-a/v7/output/"
    assert output.analysers == {"mq": "ok", "dq": "ok"}
    assert output.reason is None


def test_activation_output_with_rejection_reason():
    """ActivationOutput should support reason field for rejections."""
    output = ActivationOutput(
        activate=False,
        baseline_prefix=None,
        analysers=None,
        reason="Model version not found in baseline registry",
    )
    assert output.activate is False
    assert output.reason == "Model version not found in baseline registry"


def test_activation_lambda_handler_signature():
    """activation should accept event, context and return dict."""
    import inspect

    sig = inspect.signature(activation)
    params = list(sig.parameters.keys())
    assert "event" in params
    assert "context" in params


@mock_aws
def test_activation_returns_true_for_approved_baseline(monkeypatch: pytest.MonkeyPatch):
    """AC-E1-A1: approved baseline → activate=True + baseline_prefix + analysers."""
    # Setup DynamoDB table
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.create_table(
        TableName="baseline-registry",
        KeySchema=[
            {"AttributeName": "project", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "project", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # Seed an approved baseline
    table.put_item(
        Item={
            "project": "project-a",
            "sk": "v7",
            "status": "approved",
            "baseline_prefix": "s3://baselines/project-a/v7/output/",
            "analysers": {"mq": "ok", "dq": "ok"},
            "manifest_uri": "s3://baselines/project-a/v7/input/manifest.json",
            "evaluated_at": datetime.now(UTC).isoformat(),
            "sfn_execution_arn": "arn:aws:states:eu-west-1:123456789012:execution:baseline-sfn:abc123",
        }
    )

    monkeypatch.setenv("BASELINE_REGISTRY_TABLE_NAME", "baseline-registry")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")

    result = activation(
        {"project": "project-a", "model_version": "7"},
        None,
    )

    assert result["activate"] is True
    assert result["baseline_prefix"] == "s3://baselines/project-a/v7/output/"
    assert result["analysers"] == {"mq": "ok", "dq": "ok"}
    assert result["reason"] is None


@mock_aws
def test_activation_returns_false_for_rejected_baseline(monkeypatch: pytest.MonkeyPatch):
    """AC-E1-A2: rejected baseline → activate=False + reason."""
    # Setup DynamoDB table
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.create_table(
        TableName="baseline-registry",
        KeySchema=[
            {"AttributeName": "project", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "project", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # Seed a rejected baseline
    table.put_item(
        Item={
            "project": "project-a",
            "sk": "v7",
            "status": "rejected",
            "baseline_prefix": "s3://baselines/project-a/v7/output/",
            "analysers": {"mq": "failed", "dq": "ok"},
            "manifest_uri": "s3://baselines/project-a/v7/input/manifest.json",
            "evaluated_at": datetime.now(UTC).isoformat(),
            "sfn_execution_arn": "arn:aws:states:eu-west-1:123456789012:execution:baseline-sfn:abc123",
        }
    )

    monkeypatch.setenv("BASELINE_REGISTRY_TABLE_NAME", "baseline-registry")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")

    result = activation(
        {"project": "project-a", "model_version": "7"},
        None,
    )

    assert result["activate"] is False
    assert result["reason"] == "Baseline rejected"
    assert result["baseline_prefix"] is None
    assert result["analysers"] is None


@mock_aws
def test_activation_returns_false_for_missing_version(monkeypatch: pytest.MonkeyPatch):
    """AC-E1-A3: missing model version → activate=False + reason."""
    # Setup DynamoDB table (empty)
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    dynamodb.create_table(
        TableName="baseline-registry",
        KeySchema=[
            {"AttributeName": "project", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "project", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    monkeypatch.setenv("BASELINE_REGISTRY_TABLE_NAME", "baseline-registry")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")

    result = activation(
        {"project": "project-a", "model_version": "99"},
        None,
    )

    assert result["activate"] is False
    assert "not found" in result["reason"].lower()
    assert result["baseline_prefix"] is None
    assert result["analysers"] is None


def test_activation_missing_env_var_raises(monkeypatch: pytest.MonkeyPatch):
    """AC-E1-A4: missing BASELINE_REGISTRY_TABLE_NAME → raises KeyError."""
    monkeypatch.delenv("BASELINE_REGISTRY_TABLE_NAME", raising=False)

    with pytest.raises(KeyError):
        activation(
            {"project": "project-a", "model_version": "7"},
            None,
        )
