"""Integration tests for WriteRegistry Lambda — DynamoDB write."""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from model_monitor_cdk.write_registry import write_registry
from moto import mock_aws


@pytest.fixture
def sample_event() -> dict:
    """Create a sample event for write_registry Lambda."""
    return {
        "project": "project-a",
        "model_version": "7",
        "status": "approved",
        "baseline_prefix": "s3://baselines/project-a/v7/output/",
        "analysers": {"mq": "ok", "dq": "ok", "shadow": "ok"},
        "manifest_uri": "s3://baselines/project-a/v7/input/manifest.json",
        "sfn_execution_arn": "arn:aws:states:eu-west-1:123456789012:execution:baseline-sfn:abc123",
    }


@mock_aws
def test_write_registry_writes_to_ddb_on_success(sample_event):
    """WriteRegistry should write baseline record to DynamoDB successfully."""
    # Create DynamoDB table
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table_name = "test-baseline-registry"
    table = dynamodb.create_table(
        TableName=table_name,
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

    # Call write_registry with env var set
    with patch.dict(
        os.environ,
        {
            "BASELINE_REGISTRY_TABLE_NAME": table_name,
            "AWS_DEFAULT_REGION": "eu-west-1",
        },
    ):
        result = write_registry(sample_event, None)

    # Assert: return success
    assert result["written"] is True
    assert result["project"] == "project-a"
    assert result["model_version"] == "7"

    # Query back from DynamoDB
    response = table.get_item(Key={"project": "project-a", "sk": "v7"})
    item = response["Item"]

    # Verify all fields are present and correct
    assert item["project"] == "project-a"
    assert item["sk"] == "v7"
    assert item["status"] == "approved"
    assert item["baseline_prefix"] == "s3://baselines/project-a/v7/output/"
    assert item["analysers"] == {"mq": "ok", "dq": "ok", "shadow": "ok"}
    assert item["manifest_uri"] == "s3://baselines/project-a/v7/input/manifest.json"
    assert item["sfn_execution_arn"] == "arn:aws:states:eu-west-1:123456789012:execution:baseline-sfn:abc123"

    # Verify evaluated_at is a parseable ISO8601 string
    assert "evaluated_at" in item
    assert isinstance(item["evaluated_at"], str)
    # Basic ISO8601 format check
    datetime.fromisoformat(item["evaluated_at"])


@mock_aws
def test_write_registry_missing_env_var_raises_keyerror(sample_event):
    """WriteRegistry should raise KeyError if BASELINE_REGISTRY_TABLE_NAME is not set."""
    # Ensure env var is not set
    with patch.dict(os.environ, {}, clear=False):
        # Remove the env var if it exists
        if "BASELINE_REGISTRY_TABLE_NAME" in os.environ:
            del os.environ["BASELINE_REGISTRY_TABLE_NAME"]

        with pytest.raises(KeyError):
            write_registry(sample_event, None)


@mock_aws
def test_write_registry_client_error_propagates(sample_event):
    """WriteRegistry should propagate ClientError when put_item fails."""
    from unittest.mock import MagicMock

    # Create DynamoDB table
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table_name = "test-baseline-registry"
    dynamodb.create_table(
        TableName=table_name,
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

    # Call write_registry with env var set, mock put_item to raise ClientError
    with (
        patch.dict(
            os.environ,
            {
                "BASELINE_REGISTRY_TABLE_NAME": table_name,
                "AWS_DEFAULT_REGION": "eu-west-1",
            },
        ),
        patch("boto3.resource") as mock_boto3,
    ):
        mock_table = MagicMock()
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "Item size exceeded"}},
            "PutItem",
        )
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.return_value = mock_resource

        # Should raise ClientError
        with pytest.raises(ClientError):
            write_registry(sample_event, None)
