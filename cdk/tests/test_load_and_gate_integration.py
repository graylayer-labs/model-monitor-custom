"""Integration tests for LoadAndGate — fetch manifest + config, run gate logic."""

from __future__ import annotations

import json
from unittest.mock import patch

from model_monitor_cdk.load_and_gate import load_and_gate
from moto import mock_aws


@mock_aws
def test_load_and_gate_fetches_manifest_from_s3():
    """LoadAndGate should fetch manifest.json from S3."""
    import boto3

    # Set up moto S3
    s3 = boto3.client("s3", region_name="eu-west-1")
    s3.create_bucket(
        Bucket="baselines",
        CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
    )

    manifest_data = {
        "schema_version": "1",
        "project": "project-a",
        "model_version": "7",
        "produced_at": "2026-07-31T10:00:00Z",
        "provenance": {"git_sha": "abc123", "pipeline_run_id": "run-1"},
        "artifacts": {
            "training_snapshot": "input/snapshot.jsonl",
            "predictions": "input/predictions.jsonl",
            "model": "input/model.tar.gz",
        },
    }
    s3.put_object(
        Bucket="baselines",
        Key="project-a/v7/input/manifest.json",
        Body=json.dumps(manifest_data),
    )

    event = {
        "manifest_uri": "s3://baselines/project-a/v7/input/manifest.json",
        "config_uri": "s3://config-prod-123456789012-eu-west-1/project-a/v1/config.json",
        "project": "project-a",
    }

    # Should not raise S3 error
    with patch.dict("os.environ", {"AWS_DEFAULT_REGION": "eu-west-1"}):
        result = load_and_gate(event, None)
        assert "status" in result


@mock_aws
def test_load_and_gate_integrates_gate_logic():
    """LoadAndGate should use gate logic to determine analysers_to_run."""
    import boto3

    s3 = boto3.client("s3", region_name="eu-west-1")
    s3.create_bucket(
        Bucket="baselines",
        CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
    )
    s3.create_bucket(
        Bucket="config-prod-123456789012-eu-west-1",
        CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
    )

    # Manifest with only MQ and DQ artifacts (bias, explain missing)
    manifest_data = {
        "schema_version": "1",
        "project": "project-a",
        "model_version": "7",
        "produced_at": "2026-07-31T10:00:00Z",
        "provenance": {"git_sha": "abc123", "pipeline_run_id": "run-1"},
        "artifacts": {
            "training_snapshot": "input/snapshot.jsonl",
            "predictions": "input/predictions.jsonl",
            "model": "input/model.tar.gz",
            "evaluation_results": "input/evaluation.json",
        },
    }
    s3.put_object(
        Bucket="baselines",
        Key="project-a/v7/input/manifest.json",
        Body=json.dumps(manifest_data),
    )

    # Config with MQ required, bias disabled, DQ optional
    config_data = {
        "name": "project-a",
        "inference_account": "123456789012",
        "producer_bucket_arn": "arn:aws:s3:::test",
        "monitors": {
            "model_quality": {"enabled": True, "required": True},
            "data_quality": {"enabled": True, "required": False},
            "bias": {"enabled": False},
            "explainability": {"enabled": False},
            "shadow": {"enabled": True},
        },
    }
    s3.put_object(
        Bucket="config-prod-123456789012-eu-west-1",
        Key="project-a/v1/config.json",
        Body=json.dumps(config_data),
    )

    event = {
        "manifest_uri": "s3://baselines/project-a/v7/input/manifest.json",
        "config_uri": "s3://config-prod-123456789012-eu-west-1/project-a/v1/config.json",
        "project": "project-a",
    }

    with patch.dict("os.environ", {"AWS_DEFAULT_REGION": "eu-west-1"}):
        result = load_and_gate(event, None)

        # Should have model_quality, data_quality, shadow (bias disabled, explain disabled)
        # Note: gate logic returns full monitor names from config keys
        assert "analysers_to_run" in result
        expected = {"model_quality", "data_quality", "shadow"}
        actual = set(result["analysers_to_run"])
        assert actual == expected, f"Expected {expected}, got {actual}"


@mock_aws
def test_load_and_gate_rejects_on_missing_required_artifact():
    """LoadAndGate should reject if required monitor artifact is missing."""
    import boto3

    s3 = boto3.client("s3", region_name="eu-west-1")
    s3.create_bucket(
        Bucket="baselines",
        CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
    )
    s3.create_bucket(
        Bucket="config-prod-123456789012-eu-west-1",
        CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
    )

    # Manifest missing MQ artifacts
    manifest_data = {
        "schema_version": "1",
        "project": "project-a",
        "model_version": "7",
        "produced_at": "2026-07-31T10:00:00Z",
        "provenance": {"git_sha": "abc123", "pipeline_run_id": "run-1"},
        "artifacts": {
            "evaluation_results": "input/evaluation.json",
        },
    }
    s3.put_object(
        Bucket="baselines",
        Key="project-a/v7/input/manifest.json",
        Body=json.dumps(manifest_data),
    )

    # Config with MQ required
    config_data = {
        "name": "project-a",
        "inference_account": "123456789012",
        "producer_bucket_arn": "arn:aws:s3:::test",
        "monitors": {
            "model_quality": {"enabled": True, "required": True},
            "data_quality": {"enabled": True, "required": False},
            "bias": {"enabled": False},
            "explainability": {"enabled": False},
            "shadow": {"enabled": False},
        },
    }
    s3.put_object(
        Bucket="config-prod-123456789012-eu-west-1",
        Key="project-a/v1/config.json",
        Body=json.dumps(config_data),
    )

    event = {
        "manifest_uri": "s3://baselines/project-a/v7/input/manifest.json",
        "config_uri": "s3://config-prod-123456789012-eu-west-1/project-a/v1/config.json",
        "project": "project-a",
    }

    with patch.dict("os.environ", {"AWS_DEFAULT_REGION": "eu-west-1"}):
        result = load_and_gate(event, None)

        # Should be rejected due to missing required MQ artifacts
        assert result["status"] == "rejected"
        assert "model_quality" in result["message"].lower() or "required" in result["message"].lower()
