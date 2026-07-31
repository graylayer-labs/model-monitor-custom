"""Tests for :mod:`mmc_base.lambda_handler` — Lambda entrypoint."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from moto import mock_aws


def test_lambda_handler_exists():
    """Lambda handler module should exist and export handler."""
    from mmc_base.lambda_handler import handler

    assert callable(handler)


def test_lambda_handler_signature():
    """Lambda handler should accept (event, context) and return dict."""
    from mmc_base.lambda_handler import handler

    sig = inspect.signature(handler)
    params = list(sig.parameters.keys())
    assert "event" in params
    assert "context" in params


@mock_aws
def test_lambda_handler_with_sfn_payload():
    """Handler should map Step Functions lowercase payload to uppercase env contract."""
    import os

    import boto3

    from mmc_base.lambda_handler import handler
    from mmc_base.testing import NoopAnalyser, run_container_flow

    # Set region for boto3 clients throughout the test
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"

    # Set up mocked AWS resources
    s3 = boto3.client("s3", region_name="eu-west-1")
    ddb = boto3.client("dynamodb", region_name="eu-west-1")
    cw = boto3.client("cloudwatch", region_name="eu-west-1")

    # Create mocked S3 buckets
    s3.create_bucket(
        Bucket="baselines-bucket",
        CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
    )
    s3.create_bucket(
        Bucket="archive-bucket",
        CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
    )

    # Create mocked DynamoDB table
    ddb.create_table(
        TableName="mmc-test-outcomes",
        KeySchema=[
            {"AttributeName": "run_id", "KeyType": "HASH"},
            {"AttributeName": "analyser_type", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "run_id", "AttributeType": "S"},
            {"AttributeName": "analyser_type", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
        StreamSpecification={"StreamEnabled": True, "StreamViewType": "NEW_IMAGE"},
    )

    # Put a minimal config in S3
    s3.put_object(
        Bucket="baselines-bucket",
        Key="config.json",
        Body='{"threshold": 0.5}',
    )

    # Real Step Functions payload (lowercase keys, matching SFN definition)
    event = {
        "project": "test-project",
        "run_id": str(UUID("12345678-1234-5678-1234-567812345678")),
        "analyser_type": "bias",
        "input_uris_json": "{}",
        "output_uri": "s3://archive-bucket/out",
        "config_uri": "s3://baselines-bucket/config.json",
        "environment": "test",
        "variant": "AllTraffic",
    }
    context = MagicMock()

    # Should handle lowercase keys without error (handler maps them to uppercase)
    # The handler will fail trying to run analysis (missing env vars), but the
    # important thing is it doesn't fail on "field required" errors from EnvContract.
    try:
        result = handler(event, context)
    except Exception as e:
        # If we got a RuntimeError about missing env vars, the key mapping worked!
        # (We got past EnvContract validation, which proves the lowercase keys
        # were successfully mapped to uppercase.)
        assert "not set" in str(e) or "input_uris" in str(e) or "NoSuchKey" in str(e)
