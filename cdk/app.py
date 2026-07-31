"""CDK entrypoint for model-monitor-custom.

Topology is declared in two YAML files (``environments/accounts.yaml`` +
``environments/projects.yaml``) — see :mod:`model_monitor_cdk.config` and
ADR ``009-config-driven-topology``. The same stack code deploys 1-, 2-, or
3-account setups: CDK filters stacks by ``env.account`` against the
deploying profile, so ``uv run cdk deploy '*' --profile <p>`` acts on
whatever the profile owns.
"""

from __future__ import annotations

import aws_cdk as cdk
from model_monitor_cdk.config import resolve_env_from_context
from model_monitor_cdk.stacks.artifact_stack import ArtifactStack, ArtifactStackProps
from model_monitor_cdk.stacks.config_stack import ConfigStack
from model_monitor_cdk.stacks.github_oidc_stack import (
    GithubOidcStack,
    GithubOidcStackProps,
)
from model_monitor_cdk.stacks.inference_monitor_stack import (
    InferenceMonitorStack,
    InferenceMonitorStackProps,
)
from model_monitor_cdk.stacks.operations_baseline_stack import (
    OperationsBaselineStack,
    OperationsBaselineStackProps,
)
from model_monitor_cdk.stacks.producer_events_stack import (
    ProducerEventsStack,
    ProducerEventsStackProps,
)
from model_monitor_cdk.stacks.shared_iam_stack import SharedIamStack, SharedIamStackProps

_ENV_TAG = "test"
_ANALYSER_NAMES = ("mq", "dq", "bias", "explain", "shadow")


def _analyser_image_uris(*, artifact_account: str, region: str) -> dict[str, str]:
    """Build the analyser ECR URI map keyed by analyser type.

    Args:
        artifact_account: 12-digit ml-artifact account ID (ECR host).
        region: AWS region for the ECR host.

    Returns:
        Analyser type → ``<host>/mmc/analyser-<name>:latest`` URI.
    """
    host = f"{artifact_account}.dkr.ecr.{region}.amazonaws.com"
    return {name: f"{host}/mmc/analyser-{name}:latest" for name in _ANALYSER_NAMES}


def build_app(app: cdk.App) -> cdk.App:
    """Instantiate every stack the loaded topology declares.

    Args:
        app: A fresh CDK ``App`` — its context is used to locate the
            YAML config files.

    Returns:
        The same ``app`` with stacks attached (for tests).
    """
    cfg = resolve_env_from_context(app)
    accounts = cfg.accounts
    roles = accounts.roles
    region = accounts.region
    analyser_images = _analyser_image_uris(artifact_account=roles.artifact, region=region)
    writer_role_arn = f"arn:aws:iam::{roles.artifact}:role/mmc-{_ENV_TAG}-baseline-writer"

    artifact = ArtifactStack(
        app,
        f"MMC-{_ENV_TAG.capitalize()}-Artifact",
        props=ArtifactStackProps(
            environment=_ENV_TAG,
            consumer_account_ids=list(roles.inference),
            operations_account_id=roles.operations,
        ),
        env=cdk.Environment(account=roles.artifact, region=region),
    )

    config_stack = ConfigStack(
        app,
        f"MMC-{_ENV_TAG.capitalize()}-Config",
        region=region,
        environment=_ENV_TAG,
        env=cdk.Environment(account=roles.artifact, region=region),
    )

    if accounts.github_oidc is not None:
        oidc = accounts.github_oidc
        GithubOidcStack(
            app,
            f"MMC-{_ENV_TAG.capitalize()}-GithubOidc",
            props=GithubOidcStackProps(
                environment=_ENV_TAG,
                github_repo=oidc.github_repo,
                ref_filter=oidc.ref_filter,
                create_oidc_provider=oidc.create_provider,
            ),
            env=cdk.Environment(account=roles.artifact, region=region),
        )

    SharedIamStack(
        app,
        f"MMC-{_ENV_TAG.capitalize()}-SharedIam",
        props=SharedIamStackProps(
            environment=_ENV_TAG,
            reader_accounts=list(roles.inference),
            writer_account_id=roles.operations,
            baselines_bucket_arn=artifact.baselines_bucket.bucket_arn,
            artifact_kms_key_arn=artifact.kms_key.key_arn,
        ),
        env=cdk.Environment(account=roles.artifact, region=region),
    )

    for project in cfg.projects.projects:
        InferenceMonitorStack(
            app,
            f"MMC-{_ENV_TAG.capitalize()}-InferenceMonitor-{project.name}",
            props=InferenceMonitorStackProps(
                environment=_ENV_TAG,
                project_name=project.name,
                consumer_account_id=project.inference_account,
                artifact_account_id=roles.artifact,
                artifact_kms_key_arn=artifact.kms_key.key_arn,
                baselines_bucket_arn=artifact.baselines_bucket.bucket_arn,
                analyser_image_uris=analyser_images,
                vpc_id=project.vpc_id,
                schedule_expression=project.schedule,
                compute_backend=project.compute_backend,
            ),
            env=cdk.Environment(account=project.inference_account, region=region),
        )

        OperationsBaselineStack(
            app,
            f"MMC-{_ENV_TAG.capitalize()}-OperationsBaseline-{project.name}",
            props=OperationsBaselineStackProps(
                environment=_ENV_TAG,
                project_name=project.name,
                operations_account_id=roles.operations,
                artifact_account_id=roles.artifact,
                baselines_bucket_arn=artifact.baselines_bucket.bucket_arn,
                artifact_kms_key_arn=artifact.kms_key.key_arn,
                baseline_writer_role_arn=writer_role_arn,
                producer_bucket_arn=project.producer_bucket_arn,
                producer_account_id=project.producer_account,
                analyser_image_uris=analyser_images,
                vpc_id=accounts.operations_vpc_id,
                compute_backend=project.compute_backend,
            ),
            env=cdk.Environment(account=roles.operations, region=region),
        )

        if project.producer_account is not None and project.producer_account != roles.operations:
            ProducerEventsStack(
                app,
                f"MMC-{_ENV_TAG.capitalize()}-ProducerEvents-{project.name}",
                props=ProducerEventsStackProps(
                    environment=_ENV_TAG,
                    project_name=project.name,
                    producer_bucket_arn=project.producer_bucket_arn,
                    producer_prefix="training-snapshots/",
                    operations_account_id=roles.operations,
                    operations_region=region,
                ),
                env=cdk.Environment(account=project.producer_account, region=region),
            )
    return app


if __name__ == "__main__":
    build_app(cdk.App()).synth()
