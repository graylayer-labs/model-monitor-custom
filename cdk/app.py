"""CDK entrypoint for model-monitor-custom.

Topology is declared in two YAML files (``environments/accounts.yaml`` +
``environments/projects.yaml``) — see :mod:`model_monitor_cdk.config` and
ADR ``009-config-driven-topology``. The same stack code deploys 1-, 2-, or
3-account setups: CDK filters stacks by ``env.account`` against the
deploying profile, so ``uv run cdk deploy '*' --profile <p>`` acts on
whatever the profile owns.
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_lambda as lambda_
from model_monitor_cdk.stacks.inference_monitor_stack import (
    InferenceMonitorStack,
    InferenceMonitorStackProps,
)

_ENV_TAG = "e2e"
_PROJECT_NAME = "mmc-aws-test"
_ANALYSER_NAMES = ("mq", "dq", "bias", "explain", "shadow")


def _is_localstack_mode(scope: cdk.App) -> bool:
    """Detect if running against LocalStack (from cdk.json context)."""
    return scope.node.try_get_context("localstack") is True


def _localstack_image_source(analyser: str) -> lambda_.DockerImageCode:
    """Build Docker image code from local image for LocalStack testing.

    For LocalStack, we use pre-built local Docker images (mmc-{analyser}-lambda:latest)
    instead of trying to fetch from ECR or publish assets. This avoids S3 asset
    publishing issues that occur when using container images with CDK + LocalStack.

    Args:
        analyser: Analyser type (mq, dq, bias, explain, shadow)

    Returns:
        DockerImageCode referencing the local Docker image
    """
    # Local Docker images are pre-built by the test runner or user
    # Reference them by name; Lambda will use the local Docker daemon
    return lambda_.DockerImageCode.from_image_asset(
        directory=str(Path(__file__).parent.parent / f"containers/{analyser}"),
        file="Dockerfile.lambda",
        build_args={
            "BASE_IMAGE": "mmc-base-lambda:latest",  # Pre-built base image
        },
    )




def build_app(app: cdk.App) -> cdk.App:
    """Instantiate the inference monitor stack for testing.

    AWS automatically provides account ID and region from credentials.
    No configuration files needed.

    Args:
        app: A fresh CDK ``App``.

    Returns:
        The same ``app`` with stacks attached.
    """
    localstack_mode = _is_localstack_mode(app)
    image_source = _localstack_image_source if localstack_mode else None

    InferenceMonitorStack(
        app,
        f"MMC-{_ENV_TAG.capitalize()}-InferenceMonitor",
        props=InferenceMonitorStackProps(
            environment=_ENV_TAG,
            project_name=_PROJECT_NAME,
            consumer_account_id="",
            artifact_account_id="",
            artifact_kms_key_arn="",
            baselines_bucket_arn="",
            analyser_image_uris={name: f"mmc-{name}-lambda:latest" for name in _ANALYSER_NAMES},
            vpc_id=None,
            schedule_expression="cron(0 * * * ? *)",
            compute_backend="lambda",
            analyser_image_source=image_source,
        ),
    )

    return app


if __name__ == "__main__":
    build_app(cdk.App()).synth()
