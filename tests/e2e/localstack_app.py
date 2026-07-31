"""Minimal CDK app for LocalStack e2e testing.

Deploys only InferenceMonitorStack with Lambda backend, event_wiring disabled,
and local Docker image loading. KMS ARN and baselines bucket ARN are injected
via environment variables (provided by the conftest fixture).
"""

from __future__ import annotations

import os

import aws_cdk as cdk
from aws_cdk import aws_lambda as lambda_

from model_monitor_cdk.stacks.inference_monitor_stack import (
    InferenceMonitorStack,
    InferenceMonitorStackProps,
)


def local_image_loader(analyser: str) -> lambda_.DockerImageCode:
    """Load analyser container image from local Dockerfile.lambda.

    Used by LocalStack tests to build images locally instead of pulling from ECR.
    Each image is built with BASE_IMAGE=mmc-base-lambda:latest (pre-built by conftest).
    """
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    container_dir = repo_root / "containers" / analyser

    return lambda_.DockerImageCode.from_image_asset(
        directory=str(container_dir),
        file="Dockerfile.lambda",
        build_args={"BASE_IMAGE": "mmc-base-lambda:latest"},
        cmd=["mmc_base.lambda_handler.handler"],
    )


def build_app(app: cdk.App) -> None:
    """Instantiate InferenceMonitorStack for LocalStack e2e testing."""
    kms_key_arn = os.environ.get("MMC_TEST_KMS_KEY_ARN", "arn:aws:kms:eu-west-1:000000000000:key/test")
    baselines_bucket_arn = os.environ.get("MMC_TEST_BASELINES_BUCKET_ARN", "arn:aws:s3:::mmc-test-baselines")

    InferenceMonitorStack(
        app,
        "MMC-Test-InferenceMonitor",
        props=InferenceMonitorStackProps(
            environment="test",
            project_name="test-project",
            consumer_account_id="000000000000",
            artifact_account_id="000000000000",
            artifact_kms_key_arn=kms_key_arn,
            baselines_bucket_arn=baselines_bucket_arn,
            analyser_image_uris={a: f"dummy-{a}" for a in ("mq", "dq", "bias", "explain", "shadow")},
            compute_backend="lambda",
            enable_event_wiring=False,
            analyser_image_source=local_image_loader,
        ),
        env=cdk.Environment(account="000000000000", region="eu-west-1"),
    )


if __name__ == "__main__":
    build_app(cdk.App())
