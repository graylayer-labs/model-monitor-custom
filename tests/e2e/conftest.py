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
                # Check that key services are ready
                if health.get("services", {}).get("s3") == "running":
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
    """Build the base Dockerfile.lambda image (analyser images built by CDK)."""
    repo_root = Path(__file__).parent.parent.parent
    base_dockerfile = repo_root / "containers" / "base" / "Dockerfile.lambda"

    # Build base image
    subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(base_dockerfile),
            "-t",
            "mmc-base-lambda:latest",
            str(base_dockerfile.parent),
        ],
        check=True,
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

    # Create KMS key
    key_resp = kms.create_key(Description="MMC test key")
    key_arn = key_resp["KeyMetadata"]["Arn"]

    # Create baselines bucket
    s3.create_bucket(
        Bucket="mmc-test-baselines",
        CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
    )

    # Create outcomes table (created by CDK, but pre-create for determinism if needed)
    # (CDK will create it; we just seed data)

    # Export ARNs for the CDK app
    os.environ["MMC_TEST_KMS_KEY_ARN"] = key_arn
    os.environ["MMC_TEST_BASELINES_BUCKET_ARN"] = "arn:aws:s3:::mmc-test-baselines"

    return {"key_arn": key_arn, "bucket_arn": "arn:aws:s3:::mmc-test-baselines", "s3": s3, "kms": kms}


# Skip all e2e tests unless explicitly enabled
pytestmark = pytest.mark.skipif(
    not os.environ.get("LOCALSTACK_TEST_ENABLED"),
    reason="LocalStack e2e tests require LOCALSTACK_TEST_ENABLED=1",
)
