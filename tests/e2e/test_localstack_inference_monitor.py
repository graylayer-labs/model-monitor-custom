"""End-to-end test: full monitoring stack on LocalStack."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import UUID

import boto3
import pytest


@pytest.mark.e2e
def test_full_inference_monitor_fan_out(localstack_resources):
    """End-to-end: deploy stack, run analysis, verify outcomes."""
    repo_root = Path(__file__).parent.parent.parent
    test_cdk_dir = repo_root / "tests" / "e2e"
    region = "eu-west-1"

    # Set up boto3 clients for LocalStack
    os.environ["AWS_ACCESS_KEY_ID"] = "test"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
    os.environ["AWS_DEFAULT_REGION"] = region

    s3 = boto3.client("s3", endpoint_url="http://localhost:4566", region_name=region)
    ddb = boto3.client("dynamodb", endpoint_url="http://localhost:4566", region_name=region)
    sfn = boto3.client("stepfunctions", endpoint_url="http://localhost:4566", region_name=region)

    # 1. Bootstrap CDK for LocalStack
    subprocess.run(
        ["cdklocal", "bootstrap", "--profile", "localstack"],
        cwd=str(test_cdk_dir),
        env={**os.environ, "AWS_PROFILE": "localstack"},
        check=True,
    )

    # 2. Deploy the test CDK app
    deploy_output = subprocess.run(
        ["cdklocal", "deploy", "--require-approval", "never"],
        cwd=str(test_cdk_dir),
        capture_output=True,
        text=True,
        check=True,
    )

    # 3. Extract stack outputs (state machine ARN)
    # Parse CloudFormation outputs from deploy output
    sfn_arn = None
    for line in deploy_output.stdout.split("\n"):
        if "StateMachineArn" in line:
            sfn_arn = line.split("=")[-1].strip()
            break

    if not sfn_arn:
        raise RuntimeError("Could not find StateMachineArn in deploy output")

    # 4. Seed fixture data (config.json + input snapshot)
    config_data = {"threshold": 0.5}
    s3.put_object(
        Bucket="mmc-test-baselines",
        Key="config.json",
        Body=json.dumps(config_data),
    )

    # For a minimal test, create a dummy input parquet (in practice, copy from examples/adult-classifier)
    # For now, just seed a marker that tests can look for
    s3.put_object(
        Bucket="mmc-test-baselines",
        Key="input/test-input.parquet",
        Body=b"dummy-parquet-content",
    )

    # 5. Start SFN execution
    run_id = str(UUID("12345678-1234-5678-1234-567812345678"))
    execution_input = {
        "project": "test-project",
        "run_id": run_id,
        "analyser_type": "mq",  # This will be overridden per-branch in the Parallel
        "input_uris_json": '{"inputs": "s3://mmc-test-baselines/input/test-input.parquet"}',
        "output_uri": "s3://mmc-test-baselines/out",
        "config_uri": "s3://mmc-test-baselines/config.json",
        "environment": "test",
        "variant": "AllTraffic",
    }

    exec_resp = sfn.start_execution(
        stateMachineArn=sfn_arn,
        input=json.dumps(execution_input),
    )
    execution_arn = exec_resp["executionArn"]

    # 6. Poll for completion (30s timeout, 1s interval)
    start_time = time.time()
    timeout = 30
    while time.time() - start_time < timeout:
        exec_status = sfn.describe_execution(executionArn=execution_arn)
        status = exec_status["status"]

        if status == "SUCCEEDED":
            break
        elif status == "FAILED":
            raise AssertionError(f"SFN execution failed: {exec_status.get('cause', 'unknown')}")

        time.sleep(1)
    else:
        raise TimeoutError(f"SFN execution did not complete within {timeout}s")

    # 7. Verify outcomes in DynamoDB
    # Query the outcomes table for all rows with this run_id
    response = ddb.query(
        TableName="mmc-test-outcomes",
        KeyConditionExpression="run_id = :run_id",
        ExpressionAttributeValues={":run_id": {"S": run_id}},
    )

    items = response.get("Items", [])
    assert len(items) == 5, f"Expected 5 outcome rows, got {len(items)}"

    # Verify each outcome is a valid Outcome enum value
    valid_outcomes = {
        "succeeded",
        "succeeded_with_violations",
        "skipped_no_input",
        "skipped_insufficient_data",
        "failed_handled",
        "failed_unhandled",
    }

    analyser_outcomes = {}
    for item in items:
        analyser_type = item["analyser_type"]["S"]
        outcome = item["outcome"]["S"]

        assert outcome in valid_outcomes, f"Invalid outcome '{outcome}' for {analyser_type}"
        analyser_outcomes[analyser_type] = outcome

    # Verify we have one outcome per analyser
    expected_analysers = {"mq", "dq", "bias", "explain", "shadow"}
    assert set(analyser_outcomes.keys()) == expected_analysers, f"Got analysers {set(analyser_outcomes.keys())}, expected {expected_analysers}"
