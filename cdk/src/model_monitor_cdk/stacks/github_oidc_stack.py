"""GithubOidcStack — GitHub Actions → ECR push role.

Deployed once in the artifact account (opt-in via ``accounts.yaml``).
Creates:

- The GitHub OIDC provider (if not already present in the account).
- One IAM role trusted by ``token.actions.githubusercontent.com``, scoped
  to a specific ``owner/repo`` and optional git ref filter, with least-
  privilege ECR push on the analyser repos.

The role ARN is emitted as a stack output for wiring into the GitHub
Actions workflow's ``AWS_OIDC_ROLE_ARN`` variable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from aws_cdk import CfnOutput, Stack, Tags
from aws_cdk import (
    aws_iam as iam,
)
from constructs import Construct

_GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"
_GITHUB_OIDC_AUD = "sts.amazonaws.com"
_GITHUB_OIDC_THUMBPRINT = "6938fd4d98bab03faadb97b34396831e3780aea1"
_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, kw_only=True)
class GithubOidcStackProps:
    """Configuration for :class:`GithubOidcStack`.

    Attributes:
        environment: Deployment environment tag.
        github_repo: ``owner/repo`` slug allowed to assume the role.
        ref_filter: Sub-claim ref filter, e.g. ``refs/heads/main`` or
            ``refs/tags/*``. Wildcards allowed.
        ecr_repo_names: ECR repository names (without registry prefix) the
            role is allowed to push to. Defaults to the six MMC analyser
            repos (``mmc/analyser-<name>`` + ``mmc/analyser-base``).
        create_oidc_provider: When ``True`` the stack creates the GitHub
            OIDC provider. Set ``False`` if the account already has one
            (AWS rejects duplicates).
    """

    environment: str
    github_repo: str
    ref_filter: str = "refs/heads/main"
    ecr_repo_names: tuple[str, ...] = (
        "mmc/analyser-base",
        "mmc/analyser-bias",
        "mmc/analyser-dq",
        "mmc/analyser-mq",
        "mmc/analyser-explain",
        "mmc/analyser-shadow",
    )
    create_oidc_provider: bool = True
    extra_tags: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate props at construction.

        Raises:
            ValueError: If any field fails validation.
        """
        if not self.environment:
            msg = "environment must be non-empty"
            raise ValueError(msg)
        if not _REPO_PATTERN.match(self.github_repo):
            msg = f"github_repo must be 'owner/repo', got: {self.github_repo!r}"
            raise ValueError(msg)
        if not self.ref_filter:
            msg = "ref_filter must be non-empty (e.g. 'refs/heads/main')"
            raise ValueError(msg)
        if not self.ecr_repo_names:
            msg = "ecr_repo_names must contain at least one repo"
            raise ValueError(msg)


class GithubOidcStack(Stack):
    """GitHub Actions OIDC provider + ECR-push role."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: GithubOidcStackProps,
        **kwargs: Any,  # ruff: ignore[any-type]
    ) -> None:
        """Wire the OIDC provider and push role.

        Args:
            scope: Parent construct.
            construct_id: CDK construct id.
            props: Validated configuration.
            **kwargs: Passed through to :class:`aws_cdk.Stack`.
        """
        super().__init__(scope, construct_id, **kwargs)
        self._props = props

        Tags.of(self).add("Component", "github-oidc")
        Tags.of(self).add("Environment", props.environment)
        for key, value in props.extra_tags.items():
            Tags.of(self).add(key, value)

        if props.create_oidc_provider:
            provider = iam.OpenIdConnectProvider(
                self,
                "GithubOidcProvider",
                url=_GITHUB_OIDC_URL,
                client_ids=[_GITHUB_OIDC_AUD],
                thumbprints=[_GITHUB_OIDC_THUMBPRINT],
            )
            provider_arn = provider.open_id_connect_provider_arn
        else:
            provider_arn = f"arn:aws:iam::{self.account}:oidc-provider/token.actions.githubusercontent.com"

        sub_claim = f"repo:{props.github_repo}:ref:{props.ref_filter}"
        federated_principal = iam.FederatedPrincipal(
            federated=provider_arn,
            conditions={
                "StringEquals": {
                    "token.actions.githubusercontent.com:aud": _GITHUB_OIDC_AUD,
                },
                "StringLike": {
                    "token.actions.githubusercontent.com:sub": sub_claim,
                },
            },
            assume_role_action="sts:AssumeRoleWithWebIdentity",
        )

        role = iam.Role(
            self,
            "GithubEcrPushRole",
            role_name=f"mmc-{props.environment}-github-ecr-push",
            assumed_by=federated_principal,  # ty: ignore[invalid-argument-type]
            description=(
                f"Assumed by GitHub Actions on {props.github_repo}@{props.ref_filter} to push analyser images to ECR."
            ),
        )

        ecr_arns = [f"arn:aws:ecr:{self.region}:{self.account}:repository/{name}" for name in props.ecr_repo_names]
        role.add_to_policy(
            iam.PolicyStatement(
                sid="EcrAuthToken",
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            ),
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="EcrPushToAnalyserRepos",
                actions=[
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:CompleteLayerUpload",
                    "ecr:InitiateLayerUpload",
                    "ecr:PutImage",
                    "ecr:UploadLayerPart",
                    "ecr:BatchGetImage",
                    "ecr:DescribeRepositories",
                ],
                resources=ecr_arns,
            ),
        )

        CfnOutput(self, "PushRoleArn", value=role.role_arn)
