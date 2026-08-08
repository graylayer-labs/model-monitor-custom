"""AWS E2E test fixtures and configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path

import boto3
import pytest


@pytest.fixture(scope="session")
def aws_config():
    """Load AWS configuration.

    Region defaults to eu-west-1 if AWS_REGION not set.
    Account ID is obtained from AWS caller identity (no env var needed).
    Credentials managed by boto3's standard credential chain.
    """
    import subprocess

    region = os.environ.get("AWS_REGION", "eu-west-1")

    # Get account ID from AWS if not in environment
    if "AWS_ACCOUNT_ID" in os.environ:
        account_id = os.environ["AWS_ACCOUNT_ID"]
    else:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
            capture_output=True,
            text=True,
            check=True,
        )
        account_id = result.stdout.strip()

    config = {
        "region": region,
        "account_id": account_id,
        "project": "mmc-aws-test",
        "model_version": "e2e-smoke-test-v1",
    }

    return config


@pytest.fixture(scope="session")
def s3_client(aws_config):
    """S3 client pointing to test region."""
    return boto3.client("s3", region_name=aws_config["region"])


@pytest.fixture(scope="session")
def ddb_client(aws_config):
    """DynamoDB client pointing to test region."""
    return boto3.client("dynamodb", region_name=aws_config["region"])


@pytest.fixture(scope="session")
def lambda_client(aws_config):
    """Lambda client pointing to test region."""
    return boto3.client("lambda", region_name=aws_config["region"])


@pytest.fixture(scope="session")
def sfn_client(aws_config):
    """Step Functions client pointing to test region."""
    return boto3.client("stepfunctions", region_name=aws_config["region"])


@pytest.fixture(scope="session")
def cloudwatch_client(aws_config):
    """CloudWatch client pointing to test region."""
    return boto3.client("cloudwatch", region_name=aws_config["region"])


@pytest.fixture(scope="session")
def test_data_paths():
    """Paths to test data files."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    return {
        "manifest": fixtures_dir / "manifest.json",
        "data_dir": fixtures_dir,
    }


@pytest.fixture(scope="session")
def aws_stack_names(aws_config):
    """Expected CloudFormation stack names deployed by CDK."""
    project = aws_config["project"]
    return {
        "artifact": f"MMC-{project.upper()}-Artifact",
        "baseline": f"MMC-{project.upper()}-OperationsBaseline",
        "monitor": f"MMC-{project.upper()}-InferenceMonitor",
        "trigger": f"MMC-{project.upper()}-BaselineTrigger",
    }


@pytest.fixture(scope="session")
def aws_resource_names(aws_config):
    """Expected AWS resource names (created by CDK stack)."""
    # Use environment tag (not project) for resource naming to match CDK stack
    env = "e2e"  # matches InferenceMonitorStack _ENV_TAG
    return {
        "baselines_bucket": f"mmc-{env}-baselines",
        "outcomes_table": f"mmc-{env}-outcomes",
    }


def pytest_configure(config):
    """Configure pytest markers for AWS tests."""
    config.addinivalue_line(
        "markers",
        "aws: mark test as requiring AWS credentials and account access",
    )
    config.addinivalue_line(
        "markers",
        "slow: mark test as slow (requires waiting for AWS operations)",
    )
