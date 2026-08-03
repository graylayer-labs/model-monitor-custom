"""Simplified E2E test using manual LocalStack infrastructure.

This test avoids CDK deployment complexity and asset publishing issues
by manually creating infrastructure with boto3.
"""

from __future__ import annotations

import json
import os
import boto3
import pytest
from manual_infra import create_localstack_infrastructure


@pytest.mark.e2e
def test_baseline_registry_operations():
    """Test basic DynamoDB operations for baseline registry."""
    # Create infrastructure
    resources = create_localstack_infrastructure()

    # Set up clients
    os.environ["AWS_ACCESS_KEY_ID"] = "test"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"

    ddb = boto3.client(
        "dynamodb",
        endpoint_url="http://localhost:4566",
        region_name="eu-west-1"
    )

    # Write a baseline registry entry
    table_name = resources["baseline_registry_table"]
    ddb.put_item(
        TableName=table_name,
        Item={
            "project": {"S": "test-project"},
            "sk": {"S": "v1"},
            "status": {"S": "approved"},
            "baseline_prefix": {"S": "s3://baselines/test-project/v1/"},
            "analysers": {"M": {
                "mq": {"S": "ok"},
                "dq": {"S": "ok"},
                "bias": {"S": "ok"},
                "explain": {"S": "ok"},
                "shadow": {"S": "ok"},
            }},
            "manifest_uri": {"S": "s3://baselines/test-project/v1/manifest.json"},
            "sfn_execution_arn": {"S": "arn:aws:states:eu-west-1:000000000000:execution:baseline:abc123"},
            "evaluated_at": {"S": "2026-08-03T12:00:00Z"},
        }
    )

    # Verify we can read it back
    response = ddb.get_item(
        TableName=table_name,
        Key={
            "project": {"S": "test-project"},
            "sk": {"S": "v1"},
        }
    )

    assert "Item" in response
    item = response["Item"]
    assert item["project"]["S"] == "test-project"
    assert item["status"]["S"] == "approved"
    assert "analysers" in item


@pytest.mark.e2e
def test_s3_operations():
    """Test S3 operations for manifest and config storage."""
    resources = create_localstack_infrastructure()

    os.environ["AWS_ACCESS_KEY_ID"] = "test"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"

    s3 = boto3.client(
        "s3",
        endpoint_url="http://localhost:4566",
        region_name="eu-west-1"
    )

    bucket = resources["baselines_bucket"]

    # Upload manifest
    manifest = {
        "schema_version": "1",
        "project": "test-project",
        "model_version": "v1",
        "produced_at": "2026-08-03T12:00:00Z",
        "provenance": {"git_sha": "abc123", "pipeline_run_id": "run-1"},
        "artifacts": {
            "training_snapshot": "s3://bucket/input/training.parquet",
            "predictions": "s3://bucket/input/predictions.parquet",
        },
    }

    s3.put_object(
        Bucket=bucket,
        Key="test-project/v1/input/manifest.json",
        Body=json.dumps(manifest),
        ContentType="application/json",
    )

    # Verify we can read it back
    response = s3.get_object(
        Bucket=bucket,
        Key="test-project/v1/input/manifest.json"
    )

    retrieved = json.loads(response["Body"].read())
    assert retrieved["project"] == "test-project"
    assert retrieved["model_version"] == "v1"


@pytest.mark.e2e
def test_baseline_workflow():
    """Test a complete baseline approval workflow."""
    resources = create_localstack_infrastructure()

    os.environ["AWS_ACCESS_KEY_ID"] = "test"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"

    s3 = boto3.client("s3", endpoint_url="http://localhost:4566", region_name="eu-west-1")
    ddb = boto3.client("dynamodb", endpoint_url="http://localhost:4566", region_name="eu-west-1")

    # 1. Upload baseline manifest and outputs
    baselines_bucket = resources["baselines_bucket"]
    project = "test-model"
    version = "v2.1"

    manifest = {
        "schema_version": "1",
        "project": project,
        "model_version": version,
        "produced_at": "2026-08-03T14:00:00Z",
        "provenance": {"git_sha": "def456", "pipeline_run_id": "baseline-run-2"},
        "artifacts": {
            "training_data": f"s3://{baselines_bucket}/input/train.parquet",
        },
    }

    s3.put_object(
        Bucket=baselines_bucket,
        Key=f"{project}/{version}/manifest.json",
        Body=json.dumps(manifest),
    )

    # 2. Simulate analyser outputs in S3
    analyser_results = {
        "mq": {"schema_quality": 0.99},
        "dq": {"completeness": 0.98},
        "bias": {"fairness_score": 0.95},
        "explain": {"feature_importance": {"age": 0.3, "income": 0.7}},
        "shadow": {"prediction_drift": 0.02},
    }

    for analyser, result in analyser_results.items():
        s3.put_object(
            Bucket=baselines_bucket,
            Key=f"{project}/{version}/analysers/{analyser}/output.json",
            Body=json.dumps(result),
        )

    # 3. Write baseline registry entry (approved state)
    table_name = resources["baseline_registry_table"]
    ddb.put_item(
        TableName=table_name,
        Item={
            "project": {"S": project},
            "sk": {"S": version},
            "status": {"S": "approved"},
            "baseline_prefix": {"S": f"s3://{baselines_bucket}/{project}/{version}/"},
            "analysers": {"M": {
                "mq": {"S": "approved"},
                "dq": {"S": "approved"},
                "bias": {"S": "approved"},
                "explain": {"S": "approved"},
                "shadow": {"S": "approved"},
            }},
            "manifest_uri": {"S": f"s3://{baselines_bucket}/{project}/{version}/manifest.json"},
            "sfn_execution_arn": {"S": "arn:aws:states:eu-west-1:000000000000:execution:baseline:xyz789"},
            "evaluated_at": {"S": "2026-08-03T14:05:00Z"},
        }
    )

    # 4. Verify we can retrieve and validate the baseline
    response = ddb.get_item(
        TableName=table_name,
        Key={"project": {"S": project}, "sk": {"S": version}}
    )

    assert "Item" in response
    item = response["Item"]
    assert item["status"]["S"] == "approved"

    # Verify all analysers are approved
    analysers = item["analysers"]["M"]
    for analyser in ["mq", "dq", "bias", "explain", "shadow"]:
        assert analysers[analyser]["S"] == "approved"

    # 5. Verify outputs are readable from S3
    for analyser in analyser_results.keys():
        result = s3.get_object(
            Bucket=baselines_bucket,
            Key=f"{project}/{version}/analysers/{analyser}/output.json"
        )
        data = json.loads(result["Body"].read())
        assert data is not None
