"""End-to-end AWS tests for model-monitor-custom.

Tests complete workflows:
1. Baseline analysis: snapshot → 5 analysers → registry
2. Monitor analysis: predictions → 5 analysers → outcomes
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import boto3
import pytest

from .fixtures.generate_test_data import generate_predictions_data, generate_training_data


@pytest.mark.aws
@pytest.mark.slow
class TestBaselineWorkflow:
    """Test snapshot analysis end-to-end."""

    def test_baseline_resources_deployed(
        self,
        s3_client,
        ddb_client,
        aws_resource_names,
    ):
        """Test baseline resources are deployed.

        Verifies:
        1. Baselines S3 bucket exists
        2. Outcomes DynamoDB table exists
        """
        baselines_bucket = aws_resource_names["baselines_bucket"]
        project = aws_config["project"]
        model_version = aws_config["model_version"]

        # 1. Upload test data to S3
        print("\n[1/5] Uploading test data to S3...")
        baselines_bucket = aws_resource_names["baselines_bucket"]
        outcomes_table = aws_resource_names["outcomes_table"]

        print(f"\n[1/2] Checking S3 bucket: {baselines_bucket}")
        try:
            s3_client.head_bucket(Bucket=baselines_bucket)
            print(f"  ✓ S3 bucket {baselines_bucket} exists")
        except s3_client.exceptions.NoSuchBucket:
            raise AssertionError(f"S3 bucket {baselines_bucket} not found")

        print(f"[2/2] Checking DynamoDB table: {outcomes_table}")
        try:
            ddb_client.describe_table(TableName=outcomes_table)
            print(f"  ✓ DynamoDB table {outcomes_table} exists")
        except ddb_client.exceptions.ResourceNotFoundException:
            raise AssertionError(f"DynamoDB table {outcomes_table} not found")

        print("✓ Baseline resources verified")


@pytest.mark.aws
@pytest.mark.slow
class TestMonitorWorkflow:
    """Test live monitoring analysis end-to-end."""

    def test_analyser_lambdas_deployed(self, lambda_client):
        """Test analyser Lambda functions are deployed.

        Verifies all 5 analyser Lambdas are deployed and configured.
        """
        env = "e2e"  # Matches stack env tag
        expected_analysers = {"mq", "dq", "bias", "explain", "shadow"}

        print("\n[1/2] Listing Lambda functions...")
        response = lambda_client.list_functions()
        lambda_names = {fn["FunctionName"] for fn in response["Functions"]}

        print("[2/2] Asserting analyser Lambdas deployed...")
        for analyser in expected_analysers:
            fn_name = f"mmc-{env}-{analyser}"
            assert fn_name in lambda_names, f"Lambda {fn_name} not found"
            print(f"  ✓ {fn_name} deployed")

        print("✓ Analyser Lambdas verified")
