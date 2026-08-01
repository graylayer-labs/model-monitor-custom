"""Minimal CDK app for LocalStack e2e testing.

Deploys OperationsBaselineStack + InferenceMonitorStack with Lambda backend,
event_wiring disabled, and local Docker image loading. KMS ARN and baselines
bucket ARN are injected via environment variables (provided by the conftest fixture).
"""

from __future__ import annotations

import os

import aws_cdk as cdk
from aws_cdk import aws_lambda as lambda_
from model_monitor_cdk.stacks.inference_monitor_stack import (
    InferenceMonitorStack,
    InferenceMonitorStackProps,
)
from model_monitor_cdk.stacks.operations_baseline_stack import (
    OperationsBaselineStack,
    OperationsBaselineStackProps,
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
    """Instantiate OperationsBaselineStack and InferenceMonitorStack for LocalStack e2e testing."""
    kms_key_arn = os.environ.get("MMC_TEST_KMS_KEY_ARN", "arn:aws:kms:eu-west-1:000000000000:key/test")
    baselines_bucket_arn = os.environ.get("MMC_TEST_BASELINES_BUCKET_ARN", "arn:aws:s3:::mmc-test-baselines")
    producer_bucket_arn = os.environ.get("MMC_TEST_PRODUCER_BUCKET_ARN", "arn:aws:s3:::mmc-test-producer")
    baseline_writer_role_arn = os.environ.get(
        "MMC_TEST_BASELINE_WRITER_ROLE_ARN",
        "arn:aws:iam::000000000000:role/mmc-test-baseline-writer",
    )
    baseline_registry_table_name = os.environ.get("MMC_TEST_BASELINE_REGISTRY_TABLE", "mmc-test-baseline-registry")

    # Phase F1: Deploy baseline stack (LoadAndGate → Analysers → EvaluateResults → WriteRegistry)
    OperationsBaselineStack(
        app,
        "MMC-Test-OperationsBaseline",
        props=OperationsBaselineStackProps(
            environment="test",
            project_name="test-project",
            operations_account_id="000000000000",
            artifact_account_id="000000000000",
            baselines_bucket_arn=baselines_bucket_arn,
            artifact_kms_key_arn=kms_key_arn,
            baseline_writer_role_arn=baseline_writer_role_arn,
            producer_bucket_arn=producer_bucket_arn,
            analyser_image_uris={a: f"dummy-{a}" for a in ("mq", "dq", "bias", "explain", "shadow")},
        ),
        env=cdk.Environment(account="000000000000", region="eu-west-1"),
    )

    # Phase F2: Deploy inference monitor stack (for activation + ongoing monitoring)
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
            baseline_registry_table_name=baseline_registry_table_name,
        ),
        env=cdk.Environment(account="000000000000", region="eu-west-1"),
    )


if __name__ == "__main__":
    build_app(cdk.App())
