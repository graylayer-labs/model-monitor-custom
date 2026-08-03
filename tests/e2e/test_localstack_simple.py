"""Simplified E2E test using manual LocalStack infrastructure.

This test avoids CDK deployment complexity and asset publishing issues
by manually creating infrastructure with boto3.
"""

from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path
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


@pytest.mark.e2e
def test_lambda_invocation():
    """Test Lambda function invocation in LocalStack."""
    resources = create_localstack_infrastructure()

    os.environ["AWS_ACCESS_KEY_ID"] = "test"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"

    # Set up clients
    lambda_client = boto3.client(
        "lambda",
        endpoint_url="http://localhost:4566",
        region_name="eu-west-1"
    )
    iam = boto3.client("iam", endpoint_url="http://localhost:4566", region_name="eu-west-1")

    # 1. Create IAM role for Lambda (idempotent)
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        role_resp = iam.create_role(
            RoleName="mmc-test-lambda-role",
            AssumeRolePolicyDocument=json.dumps(trust_policy),
        )
    except iam.exceptions.EntityAlreadyExistsException:
        role_resp = iam.get_role(RoleName="mmc-test-lambda-role")

    role_arn = role_resp["Role"]["Arn"]

    # 2. Create a simple Lambda function zip
    # We'll use a minimal handler that's inline in the test
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Write handler code
        handler_code = tmpdir / "lambda_function.py"
        handler_code.write_text("""
def handler(event, context):
    return {
        "statusCode": 200,
        "analyser": event.get("analyser_type", "unknown"),
        "run_id": event.get("run_id", "test"),
        "outcome": "success"
    }
""")

        # Create zip file
        zip_file = tmpdir / "lambda.zip"
        with zipfile.ZipFile(zip_file, "w") as zf:
            zf.write(handler_code, "lambda_function.py")

        # 3. Create Lambda function (idempotent)
        with open(zip_file, "rb") as f:
            zip_content = f.read()

        try:
            fn_resp = lambda_client.create_function(
                FunctionName="mmc-test-analyser",
                Runtime="python3.11",  # LocalStack supports up to 3.11
                Role=role_arn,
                Handler="lambda_function.handler",
                Code={"ZipFile": zip_content},
            )
        except lambda_client.exceptions.ResourceConflictException:
            fn_resp = lambda_client.get_function(FunctionName="mmc-test-analyser")

        function_arn = fn_resp["FunctionArn"] if "FunctionArn" in fn_resp else fn_resp["Configuration"]["FunctionArn"]

    # 4. Wait for function to be active (LocalStack async initialization)
    max_attempts = 20
    for attempt in range(max_attempts):
        try:
            fn_info = lambda_client.get_function(FunctionName=function_arn)
            if fn_info["Configuration"]["State"] == "Active":
                break
        except Exception:
            pass
        time.sleep(1)  # Increased wait time for LocalStack initialization

    # 5. Invoke the Lambda function (with retry for Pending state)
    invoke_resp = None
    for attempt in range(5):
        try:
            invoke_resp = lambda_client.invoke(
                FunctionName=function_arn,
                InvocationType="RequestResponse",
                Payload=json.dumps({
                    "analyser_type": "mq",
                    "run_id": "test-run-123"
                }),
            )
            break
        except lambda_client.exceptions.ResourceConflictException:
            time.sleep(1)

    assert invoke_resp is not None, "Lambda invocation failed after retries"

    # 6. Verify response
    assert invoke_resp["StatusCode"] == 200
    payload = json.loads(invoke_resp["Payload"].read())
    assert payload["statusCode"] == 200
    assert payload["analyser"] == "mq"
    assert payload["run_id"] == "test-run-123"
    assert payload["outcome"] == "success"
