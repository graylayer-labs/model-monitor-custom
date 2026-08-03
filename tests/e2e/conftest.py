"""Fixtures for LocalStack e2e tests."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import boto3
import pytest


@pytest.fixture(scope="session")
def localstack_up():
    """Bring up LocalStack, wait for health, and tear down."""
    repo_root = Path(__file__).parent.parent.parent
    compose_file = repo_root / "docker-compose.localstack.yml"

    # Start LocalStack
    subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "up", "-d"],
        check=True,
        cwd=str(repo_root),
    )

    # Wait for health
    max_attempts = 60
    for attempt in range(max_attempts):
        try:
            response = subprocess.run(
                ["curl", "-f", "http://localhost:4566/_localstack/health"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if response.returncode == 0:
                health = json.loads(response.stdout)
                # Check that key services are ready (status can be "running" or "available")
                s3_status = health.get("services", {}).get("s3")
                if s3_status in ("running", "available"):
                    break
        except Exception:
            pass
        time.sleep(1)
    else:
        raise TimeoutError("LocalStack did not become healthy within 60s")

    yield

    # Tear down
    subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "down", "-v"],
        check=True,
        cwd=str(repo_root),
    )


@pytest.fixture(scope="session")
def build_analyser_images(localstack_up):
    """Build the base Dockerfile.lambda image (analyser images built by CDK).

    Note: When running via localstack-test-runner.py, images are pre-built,
    but we build here to support direct pytest execution.
    """
    repo_root = Path(__file__).parent.parent.parent
    base_dockerfile = repo_root / "containers" / "base" / "Dockerfile.lambda"

    # Check if image already exists (built by test runner)
    check_result = subprocess.run(
        ["docker", "image", "inspect", "mmc-base-lambda:latest"],
        capture_output=True,
    )
    if check_result.returncode == 0:
        # Image already built, skip rebuild
        return {"base": "mmc-base-lambda:latest"}

    # Build base image from repo root (workspace context needed for dependencies)
    subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(base_dockerfile),
            "-t",
            "mmc-base-lambda:latest",
            str(repo_root),  # Build from repo root for workspace packages
        ],
        check=True,
        cwd=str(repo_root),
    )

    return {"base": "mmc-base-lambda:latest"}


@pytest.fixture(scope="function")
def localstack_resources(localstack_up, build_analyser_images):
    """Provision KMS key and baselines bucket in LocalStack, export ARNs."""
    os.environ["AWS_ACCESS_KEY_ID"] = "test"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"

    kms = boto3.client("kms", endpoint_url="http://localhost:4566", region_name="eu-west-1")
    s3 = boto3.client("s3", endpoint_url="http://localhost:4566", region_name="eu-west-1")
    ddb = boto3.client("dynamodb", endpoint_url="http://localhost:4566", region_name="eu-west-1")
    iam = boto3.client("iam", endpoint_url="http://localhost:4566", region_name="eu-west-1")

    # Create KMS key
    key_resp = kms.create_key(Description="MMC test key")
    key_arn = key_resp["KeyMetadata"]["Arn"]

    # Create baselines bucket (idempotent - skip if exists)
    try:
        s3.create_bucket(
            Bucket="mmc-test-baselines",
            CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
        )
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass

    # Create producer bucket (for baseline input)
    try:
        s3.create_bucket(
            Bucket="mmc-test-producer",
            CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
        )
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass

    # Create baseline registry table (DynamoDB) - idempotent
    registry_table_name = "mmc-test-baseline-registry"
    try:
        ddb.create_table(
            TableName=registry_table_name,
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
    except ddb.exceptions.ResourceInUseException:
        pass

    # Create baseline writer IAM role for cross-account writes - idempotent
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
    role_name = "mmc-test-baseline-writer"
    try:
        role_resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="MMC test baseline writer role",
        )
        baseline_writer_role_arn = role_resp["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role_resp = iam.get_role(RoleName=role_name)
        baseline_writer_role_arn = role_resp["Role"]["Arn"]

    # Export ARNs and names for the CDK app
    os.environ["MMC_TEST_KMS_KEY_ARN"] = key_arn
    os.environ["MMC_TEST_BASELINES_BUCKET_ARN"] = "arn:aws:s3:::mmc-test-baselines"
    os.environ["MMC_TEST_PRODUCER_BUCKET_ARN"] = "arn:aws:s3:::mmc-test-producer"
    os.environ["MMC_TEST_BASELINE_WRITER_ROLE_ARN"] = baseline_writer_role_arn
    os.environ["MMC_TEST_BASELINE_REGISTRY_TABLE"] = registry_table_name

    return {
        "key_arn": key_arn,
        "baselines_bucket": "mmc-test-baselines",
        "producer_bucket": "mmc-test-producer",
        "registry_table_name": registry_table_name,
        "baseline_writer_role_arn": baseline_writer_role_arn,
        "s3": s3,
        "kms": kms,
        "ddb": ddb,
        "iam": iam,
    }


# Skip all e2e tests unless explicitly enabled
pytestmark = pytest.mark.skipif(
    not os.environ.get("LOCALSTACK_TEST_ENABLED"),
    reason="LocalStack e2e tests require LOCALSTACK_TEST_ENABLED=1",
)
