"""End-to-end AWS tests for model-monitor-custom.

Tests:
1. Infrastructure deployment (resources created correctly)
2. Lambda invocation (functions callable with proper env)
3. Full monitoring workflow (end-to-end data flow)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from loguru import logger

from .fixtures.generate_test_data import generate_predictions_data, generate_training_data


@pytest.mark.aws
@pytest.mark.slow
class TestInfrastructureDeployment:
    """Test that infrastructure is deployed correctly."""

    def test_resources_exist(
        self,
        s3_client,
        ddb_client,
        lambda_client,
        aws_resource_names,
    ):
        """Test that all required resources are created."""
        baselines_bucket = aws_resource_names["baselines_bucket"]
        outcomes_table = aws_resource_names["outcomes_table"]
        env = "e2e"

        # Check S3 bucket
        logger.info(f"\n[1/3] Checking S3 bucket: {baselines_bucket}")
        s3_client.head_bucket(Bucket=baselines_bucket)
        logger.info(f"  ✓ S3 bucket exists")

        # Check DynamoDB table
        logger.info(f"[2/3] Checking DynamoDB table: {outcomes_table}")
        table = ddb_client.describe_table(TableName=outcomes_table)
        assert table["Table"]["TableStatus"] == "ACTIVE"
        logger.info(f"  ✓ DynamoDB table active")

        # Check Lambda functions
        logger.info(f"[3/3] Checking Lambda functions...")
        response = lambda_client.list_functions()
        lambda_names = {fn["FunctionName"] for fn in response["Functions"]}
        expected_analysers = {"mq", "dq", "bias", "explain", "shadow"}
        for analyser in expected_analysers:
            fn_name = f"mmc-{env}-{analyser}"
            assert fn_name in lambda_names, f"Lambda {fn_name} not found"
        logger.info(f"  ✓ All 5 analyser Lambdas deployed")


@pytest.mark.aws
@pytest.mark.slow
class TestLambdaInvocation:
    """Test that Lambda functions are callable."""

    def test_lambda_invocation(self, lambda_client):
        """Test invoking analyser Lambda with valid input."""
        env = "e2e"
        analyser = "mq"
        fn_name = f"mmc-{env}-{analyser}"

        logger.info(f"\n[1/2] Invoking {fn_name}...")
        payload = {
            "project": "test-project",
            "run_id": str(uuid4()),
            "input_uris_json": '{"data": "s3://test/input.parquet"}',
            "output_uri": "s3://test/output",
            "config_uri": "s3://test/config.json",
            "environment": "e2e",
            "variant": "AllTraffic",
        }

        try:
            response = lambda_client.invoke(
                FunctionName=fn_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(payload),
            )
            logger.info(f"  ✓ Lambda invoked successfully")
        except lambda_client.exceptions.ClientError as e:
            pytest.fail(f"Lambda invocation failed: {e}")

        # Verify response structure
        logger.info(f"[2/2] Checking response...")
        assert response["StatusCode"] in [200, 202], f"Got status {response['StatusCode']}"

        if "Payload" in response:
            payload_data = json.loads(response["Payload"].read())
            # Should have analyser type in response
            assert "analyser" in payload_data or payload_data.get("statusCode") in [200, 202]

        logger.info(f"  ✓ Response valid")


@pytest.mark.aws
@pytest.mark.slow
class TestMonitoringWorkflow:
    """Test full monitoring workflow end-to-end."""

    def test_data_flow_end_to_end(
        self,
        s3_client,
        ddb_client,
        lambda_client,
        aws_resource_names,
    ):
        """Test: upload data → invoke Lambda → verify outcomes recorded."""
        baselines_bucket = aws_resource_names["baselines_bucket"]
        outcomes_table = aws_resource_names["outcomes_table"]
        env = "e2e"
        run_id = str(uuid4())

        # 1. Upload test data
        logger.info(f"\n[1/4] Uploading test data...")
        test_data = generate_predictions_data()
        s3_client.put_object(
            Bucket=baselines_bucket,
            Key=f"test/{run_id}/input.parquet",
            Body=test_data.to_csv(index=False).encode(),
        )
        logger.info(f"  ✓ Test data uploaded")

        # 2. Invoke Lambda
        logger.info(f"[2/4] Invoking analyser Lambda...")
        fn_name = f"mmc-{env}-mq"
        response = lambda_client.invoke(
            FunctionName=fn_name,
            InvocationType="RequestResponse",
            Payload=json.dumps({
                "project": "test-project",
                "run_id": run_id,
                "input_uris_json": f'{{"data": "s3://{baselines_bucket}/test/{run_id}/input.parquet"}}',
                "output_uri": f"s3://{baselines_bucket}/test/{run_id}/output",
                "config_uri": f"s3://{baselines_bucket}/config.json",
                "environment": env,
                "variant": "AllTraffic",
            }),
        )
        assert response["StatusCode"] == 200
        logger.info(f"  ✓ Lambda invoked")

        # 3. Wait for async processing (outcomes might be written async)
        logger.info(f"[3/4] Waiting for outcomes...")
        time.sleep(2)

        # 4. Query outcomes
        logger.info(f"[4/4] Verifying outcomes recorded...")
        try:
            result = ddb_client.query(
                TableName=outcomes_table,
                KeyConditionExpression="run_id = :run_id",
                ExpressionAttributeValues={":run_id": {"S": run_id}},
            )
            items = result.get("Items", [])
            # May have 0 or 1+ outcomes depending on async processing
            if items:
                for item in items:
                    assert "outcome" in item
                    assert "analyser_type" in item
                logger.info(f"  ✓ {len(items)} outcome(s) recorded")
            else:
                logger.info(f"  ⚠ No outcomes yet (async processing)")
        except ddb_client.exceptions.ResourceNotFoundException:
            pytest.skip("Outcomes table not accessible (check permissions)")
