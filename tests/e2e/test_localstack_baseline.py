"""End-to-end test: baseline flow (LoadAndGate → Analysers → EvaluateResults → WriteRegistry) on LocalStack.

Phase F1: Validates the baseline snapshot and analysis workflow without
monitoring activation (Phase F2+).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import boto3
import pytest


@pytest.mark.e2e
def test_localstack_baseline_flow(localstack_resources):
    """E2E: baseline flow via LocalStack (LoadAndGate → Analysers Map → EvaluateResults → WriteRegistry).

    This test validates:
    1. CDK deployment of OperationsBaselineStack
    2. S3 seed data (manifest, config)
    3. Step Functions execution of baseline state machine
    4. DynamoDB registry write with approved status
    """
    repo_root = Path(__file__).parent.parent.parent
    test_cdk_dir = repo_root / "tests" / "e2e"
    region = "eu-west-1"

    # Ensure AWS credentials are set for LocalStack
    os.environ["AWS_ACCESS_KEY_ID"] = "test"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
    os.environ["AWS_DEFAULT_REGION"] = region

    s3 = boto3.client("s3", endpoint_url="http://localhost:4566", region_name=region)
    ddb = boto3.client("dynamodb", endpoint_url="http://localhost:4566", region_name=region)
    sfn = boto3.client("stepfunctions", endpoint_url="http://localhost:4566", region_name=region)

    baselines_bucket = localstack_resources["baselines_bucket"]
    registry_table_name = localstack_resources["registry_table_name"]

    # 1. Bootstrap CDK for LocalStack (idempotent)
    subprocess.run(
        ["cdklocal", "bootstrap", "--profile", "localstack"],
        cwd=str(test_cdk_dir),
        env={**os.environ, "AWS_PROFILE": "localstack"},
        check=True,
        capture_output=True,
    )

    # 2. Deploy the test CDK app (OperationsBaselineStack + InferenceMonitorStack)
    deploy_result = subprocess.run(
        ["cdklocal", "deploy", "--require-approval", "never"],
        cwd=str(test_cdk_dir),
        capture_output=True,
        text=True,
        check=True,
    )

    # 3. Extract baseline state machine ARN from deploy output
    sfn_arn = None
    for line in deploy_result.stdout.split("\n"):
        if "BaselineStateMachineArn" in line:
            sfn_arn = line.split("=")[-1].strip()
            break

    if not sfn_arn:
        raise RuntimeError(f"Could not find BaselineStateMachineArn in deploy output:\n{deploy_result.stdout}")

    # 4. Seed test manifest and config to S3
    project = "test-project"
    model_version = "v7"
    manifest_key = f"{project}/{model_version}/input/manifest.json"
    config_key = f"{project}/{model_version}/config.json"

    manifest_data = {
        "inputs": {
            "training": f"s3://{baselines_bucket}/{project}/{model_version}/input/training.parquet",
        },
        "production_snapshot": {
            "data": f"s3://{baselines_bucket}/{project}/{model_version}/input/production.parquet",
        },
    }

    config_data = {
        "version": "1.0",
        "analysers": {
            "mq": {"threshold": 0.5},
            "dq": {"threshold": 0.3},
            "bias": {"threshold": 0.4},
            "explain": {"threshold": 0.6},
            "shadow": {"threshold": 0.5},
        },
    }

    s3.put_object(
        Bucket=baselines_bucket,
        Key=manifest_key,
        Body=json.dumps(manifest_data),
        ContentType="application/json",
    )

    s3.put_object(
        Bucket=baselines_bucket,
        Key=config_key,
        Body=json.dumps(config_data),
        ContentType="application/json",
    )

    # 5. Start baseline state machine execution
    execution_name = f"baseline-test-{uuid4().hex[:8]}"
    execution_input = {
        "manifest_uri": f"s3://{baselines_bucket}/{manifest_key}",
        "config_uri": f"s3://{baselines_bucket}/{config_key}",
        "project": project,
        "model_version": model_version,
    }

    exec_resp = sfn.start_execution(
        stateMachineArn=sfn_arn,
        name=execution_name,
        input=json.dumps(execution_input),
    )
    execution_arn = exec_resp["executionArn"]

    # 6. Poll for execution completion (30s timeout, 1s interval)
    start_time = time.time()
    timeout = 30
    final_status = None

    while time.time() - start_time < timeout:
        exec_desc = sfn.describe_execution(executionArn=execution_arn)
        final_status = exec_desc["status"]

        if final_status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            break

        time.sleep(1)

    assert final_status == "SUCCEEDED", (
        f"Baseline execution {execution_arn} failed with status {final_status}. "
        f"Output: {exec_desc.get('output', 'N/A')}"
    )

    # 7. Verify baseline registry entry in DynamoDB
    registry_response = ddb.get_item(
        TableName=registry_table_name,
        Key={
            "project": {"S": project},
            "sk": {"S": f"v{model_version}"},
        },
    )

    assert "Item" in registry_response, (
        f"No baseline registry entry found for project={project}, model_version={model_version}"
    )

    item = registry_response["Item"]

    # Verify status is "approved"
    assert "status" in item, "Registry item missing 'status' field"
    assert item["status"]["S"] == "approved", f"Expected status='approved', got {item['status']['S']}"

    # Verify analysers field exists and has all 5 analysers
    assert "analysers" in item, "Registry item missing 'analysers' field"
    analysers = item["analysers"]["M"]
    expected_analysers = {"mq", "dq", "bias", "explain", "shadow"}
    found_analysers = set(analysers.keys())
    assert found_analysers == expected_analysers, f"Expected analysers {expected_analysers}, got {found_analysers}"

    # Verify all analysers have ok status
    for analyser_type in expected_analysers:
        status = analysers[analyser_type]["S"]
        assert status == "ok", f"Analyser {analyser_type} has status {status}, expected 'ok'"

    # Verify required fields
    required_fields = {
        "project",
        "sk",
        "status",
        "baseline_prefix",
        "analysers",
        "manifest_uri",
        "evaluated_at",
        "sfn_execution_arn",
    }
    found_fields = set(item.keys())
    assert required_fields.issubset(found_fields), f"Registry item missing fields: {required_fields - found_fields}"

    # Verify manifest_uri matches what we passed
    assert item["manifest_uri"]["S"] == f"s3://{baselines_bucket}/{manifest_key}", (
        f"Expected manifest_uri=s3://{baselines_bucket}/{manifest_key}, got {item['manifest_uri']['S']}"
    )

    # Verify sfn_execution_arn matches
    assert item["sfn_execution_arn"]["S"] == execution_arn, (
        f"Expected sfn_execution_arn={execution_arn}, got {item['sfn_execution_arn']['S']}"
    )
