"""End-to-end test: verify monitoring infrastructure on LocalStack."""

from __future__ import annotations

import json
import os
import time
from uuid import UUID

import boto3
import pytest


def _create_state_machine(sfn_client, role_arn: str) -> str:
    """Create a Step Functions state machine for analyser fan-out.

    Returns the state machine ARN.
    """
    # Parallel state with 5 branches (one per analyser type)
    definition = {
        "Comment": "Fan-out analyser execution (LocalStack test)",
        "StartAt": "AnalyserFanOut",
        "States": {
            "AnalyserFanOut": {
                "Type": "Parallel",
                "Branches": [
                    _make_analyser_branch("mq"),
                    _make_analyser_branch("dq"),
                    _make_analyser_branch("bias"),
                    _make_analyser_branch("explain"),
                    _make_analyser_branch("shadow"),
                ],
                "End": True
            }
        }
    }

    response = sfn_client.create_state_machine(
        name="mmc-e2e-test-state-machine",
        definition=json.dumps(definition),
        roleArn=role_arn,
    )
    return response["stateMachineArn"]


def _make_analyser_branch(analyser_type: str) -> dict:
    """Create a Pass state branch that simulates analyser execution."""
    return {
        "StartAt": "ExecuteAnalyser",
        "States": {
            "ExecuteAnalyser": {
                "Type": "Pass",
                "Result": {
                    "analyser": analyser_type,
                    "outcome": "succeeded"
                },
                "End": True
            }
        }
    }


@pytest.mark.e2e
def test_full_inference_monitor_fan_out(localstack_resources):
    """End-to-end: verify analyser workflow orchestration in LocalStack."""
    region = "eu-west-1"

    # Set up AWS clients for LocalStack
    os.environ["AWS_ACCESS_KEY_ID"] = "test"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
    os.environ["AWS_DEFAULT_REGION"] = region

    s3 = boto3.client("s3", endpoint_url="http://localhost:4566", region_name=region)
    ddb = boto3.client("dynamodb", endpoint_url="http://localhost:4566", region_name=region)
    sfn = boto3.client("stepfunctions", endpoint_url="http://localhost:4566", region_name=region)
    iam = boto3.client("iam", endpoint_url="http://localhost:4566", region_name=region)

    # 1. Create IAM role for Step Functions
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "states.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }
        ]
    }

    try:
        role_resp = iam.create_role(
            RoleName="mmc-e2e-sfn-role",
            AssumeRolePolicyDocument=json.dumps(trust_policy),
        )
        role_arn = role_resp["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role_resp = iam.get_role(RoleName="mmc-e2e-sfn-role")
        role_arn = role_resp["Role"]["Arn"]

    # 2. Create State Machine
    sfn_arn = _create_state_machine(sfn, role_arn)
    assert sfn_arn, "Failed to create state machine"

    # 3. Create outcomes DynamoDB table
    outcomes_table = "mmc-e2e-outcomes"
    try:
        ddb.create_table(
            TableName=outcomes_table,
            KeySchema=[
                {"AttributeName": "run_id", "KeyType": "HASH"},
                {"AttributeName": "analyser_type", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "run_id", "AttributeType": "S"},
                {"AttributeName": "analyser_type", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
    except ddb.exceptions.ResourceInUseException:
        pass

    # 4. Create baselines S3 bucket
    baselines_bucket = "mmc-e2e-baselines"
    try:
        s3.create_bucket(
            Bucket=baselines_bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass

    # Seed test data
    s3.put_object(
        Bucket=baselines_bucket,
        Key="config.json",
        Body=json.dumps({"threshold": 0.5}),
    )

    # 5. Execute State Machine
    run_id = str(UUID("12345678-1234-5678-1234-567812345678"))
    execution_input = {
        "project": "test-project",
        "run_id": run_id,
        "input_uris_json": "{}",
        "output_uri": f"s3://{baselines_bucket}/out",
        "config_uri": f"s3://{baselines_bucket}/config.json",
        "environment": "test",
        "variant": "AllTraffic",
    }

    exec_resp = sfn.start_execution(
        stateMachineArn=sfn_arn,
        input=json.dumps(execution_input),
    )
    execution_arn = exec_resp["executionArn"]

    # 6. Poll for completion
    start_time = time.time()
    timeout = int(os.environ.get("E2E_TIMEOUT_SECONDS", "60"))
    poll_interval = 1
    max_interval = 5

    while time.time() - start_time < timeout:
        exec_status = sfn.describe_execution(executionArn=execution_arn)
        status = exec_status["status"]

        if status == "SUCCEEDED":
            print(f"  ✓ SFN execution succeeded after {time.time() - start_time:.1f}s")
            break
        elif status == "FAILED":
            raise AssertionError(f"SFN execution failed: {exec_status.get('cause', 'unknown')}")
        elif status in ["TIMED_OUT", "ABORTED"]:
            raise AssertionError(f"SFN execution {status}")

        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, max_interval)
    else:
        raise TimeoutError(f"SFN execution did not complete within {timeout}s")

    # 7. Verify infrastructure is in place
    # List state machines
    sm_list = sfn.list_state_machines()
    assert len(sm_list["stateMachines"]) > 0, "No state machines found"

    # Verify S3 bucket exists
    buckets = s3.list_buckets()
    bucket_names = [b["Name"] for b in buckets.get("Buckets", [])]
    assert baselines_bucket in bucket_names, f"Bucket {baselines_bucket} not found in {bucket_names}"

    # Verify DynamoDB table exists
    tables = ddb.list_tables()
    assert outcomes_table in tables["TableNames"], f"Table {outcomes_table} not found"

    print(f"✓ LocalStack E2E test passed: state machine={sfn_arn}, bucket={baselines_bucket}, table={outcomes_table}")
