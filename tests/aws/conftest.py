"""AWS E2E test fixtures and configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path

import boto3
import pytest


@pytest.fixture(scope="session")
def aws_config():
    """Load AWS configuration from environment.

    Expected environment variables:
    - AWS_REGION: AWS region (e.g., eu-west-1)
    - AWS_ACCOUNT_ID: AWS account ID
    - AWS_ACCESS_KEY_ID: AWS credentials
    - AWS_SECRET_ACCESS_KEY: AWS credentials
    """
    config = {
        "region": os.environ.get("AWS_REGION", "eu-west-1"),
        "account_id": os.environ.get("AWS_ACCOUNT_ID"),
        "project": "mmc-aws-test",
        "model_version": "e2e-smoke-test-v1",
    }

    if not config["account_id"]:
        raise ValueError("AWS_ACCOUNT_ID environment variable required")

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
    """Expected AWS resource names."""
    project = aws_config["project"]
    return {
        "baselines_bucket": f"mmc-{project.lower()}-baselines",
        "producer_bucket": f"mmc-{project.lower()}-producer",
        "baseline_registry_table": f"mmc-{project.lower()}-baseline-registry",
        "outcomes_table": f"mmc-{project.lower()}-outcomes",
        "baseline_sfn_arn": f"arn:aws:states:{{region}}:{{account}}:stateMachine:baseline-{{project}}",
        "monitor_sfn_arn": f"arn:aws:states:{{region}}:{{account}}:stateMachine:monitor-{{project}}",
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
