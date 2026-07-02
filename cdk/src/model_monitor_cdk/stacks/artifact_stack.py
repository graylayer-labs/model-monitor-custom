"""ArtifactStack — ECR repos, baselines S3 bucket, KMS key.

Lives in the ``ml-artifact`` account. Cross-account grants are parameterised
over N ``ml-inference-*`` consumer accounts plus the ``ml-operations`` writer
account. Never reads ``self.account`` / ``self.region`` — all identity flows
in via :class:`ArtifactStackProps`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from aws_cdk import CfnOutput, Environment, RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3

if TYPE_CHECKING:
    from constructs import Construct

_ACCOUNT_ID_PATTERN = re.compile(r"^\d{12}$")
_VALID_ENVIRONMENTS = ("test", "prod", "dev")
_DEFAULT_CONTAINER_NAMES: tuple[str, ...] = (
    "analyser-base",
    "analyser-bias",
    "analyser-dq",
    "analyser-mq",
    "analyser-explain",
    "analyser-shadow",
    "baseline",
)


@dataclass(frozen=True, kw_only=True)
class ArtifactStackProps:
    """Configuration for :class:`ArtifactStack`.

    Attributes:
        environment: Deployment environment tag (``test``, ``prod``, ``dev``).
        consumer_account_ids: 1:N ml-inference-* account IDs that need
            pull + read access to ECR / baselines.
        operations_account_id: ml-operations account ID that writes baselines
            into the bucket.
        container_names: ECR repo suffixes; each becomes ``mmc/<name>``.
    """

    environment: Literal["test", "prod", "dev"]
    consumer_account_ids: list[str]
    operations_account_id: str
    container_names: tuple[str, ...] = field(default=_DEFAULT_CONTAINER_NAMES)

    def __post_init__(self) -> None:
        """Validate props on construction.

        Raises:
            ValueError: If any field fails validation.
        """
        if self.environment not in _VALID_ENVIRONMENTS:
            msg = f"environment must be one of {_VALID_ENVIRONMENTS}, got: {self.environment!r}"
            raise ValueError(msg)
        if not self.consumer_account_ids:
            msg = "consumer_account_ids must contain at least one account ID"
            raise ValueError(msg)
        for account_id in self.consumer_account_ids:
            if not _ACCOUNT_ID_PATTERN.match(account_id):
                msg = f"consumer_account_ids entries must be 12 digits, got: {account_id!r}"
                raise ValueError(msg)
        if not _ACCOUNT_ID_PATTERN.match(self.operations_account_id):
            msg = f"operations_account_id must be 12 digits, got: {self.operations_account_id!r}"
            raise ValueError(msg)
        if not self.container_names:
            msg = "container_names must contain at least one entry"
            raise ValueError(msg)


class ArtifactStack(Stack):
    """Central artifact stack for model-monitor-custom.

    Provisions:

    - One ECR repo per entry in ``props.container_names`` (``mmc/<name>``),
      with image scan-on-push enabled and pull grants to each consumer +
      the operations account.
    - One KMS CMK ``alias/mmc-<env>-artifacts`` with rotation enabled and
      decrypt granted to consumers + operations.
    - One S3 bucket (CDK-generated name) SSE'd with the CMK, public access
      blocked, and a resource policy granting read to consumers and
      read/write to the operations account.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: ArtifactStackProps,
        env: Environment | None = None,
    ) -> None:
        """Construct the stack.

        Args:
            scope: Parent construct (the CDK ``App`` or a nested stage).
                Passed through to :class:`aws_cdk.Stack`.
            construct_id: Logical ID for the stack.
            props: Validated :class:`ArtifactStackProps`.
            env: CDK ``Environment`` (account + region). Optional.
        """
        super().__init__(scope, construct_id, env=env)
        self._props = props

        consumer_principals = [iam.AccountPrincipal(a) for a in props.consumer_account_ids]
        operations_principal = iam.AccountPrincipal(props.operations_account_id)
        all_reader_principals = [*consumer_principals, operations_principal]

        key = self._build_kms_key(all_reader_principals)
        bucket = self._build_bucket(key, consumer_principals, operations_principal)
        self._build_ecr_repos(all_reader_principals)

        CfnOutput(self, "BaselinesBucketName", value=bucket.bucket_name)
        CfnOutput(self, "BaselinesBucketArn", value=bucket.bucket_arn)
        CfnOutput(self, "KmsKeyArn", value=key.key_arn)

    def _build_kms_key(self, readers: list[iam.AccountPrincipal]) -> kms.Key:
        key = kms.Key(
            self,
            "ArtifactsKey",
            alias=f"alias/mmc-{self._props.environment}-artifacts",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        for principal in readers:
            key.grant_decrypt(principal)
        return key

    def _build_bucket(
        self,
        key: kms.Key,
        consumer_principals: list[iam.AccountPrincipal],
        operations_principal: iam.AccountPrincipal,
    ) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            "BaselinesBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=key,  # ty: ignore[invalid-argument-type]
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        for principal in consumer_principals:
            bucket.add_to_resource_policy(
                iam.PolicyStatement(
                    sid=f"ConsumerRead{principal.account_id}",
                    principals=[principal],  # ty: ignore[invalid-argument-type]
                    actions=["s3:GetObject", "s3:ListBucket"],
                    resources=[bucket.bucket_arn, bucket.arn_for_objects("*")],
                ),
            )
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="OperationsReadWrite",
                principals=[operations_principal],  # ty: ignore[invalid-argument-type]
                actions=["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
                resources=[bucket.bucket_arn, bucket.arn_for_objects("*")],
            ),
        )
        return bucket

    def _build_ecr_repos(self, readers: list[iam.AccountPrincipal]) -> None:
        for name in self._props.container_names:
            repo = ecr.Repository(
                self,
                f"Repo-{name}",
                repository_name=f"mmc/{name}",
                image_scan_on_push=True,
                removal_policy=RemovalPolicy.RETAIN,
            )
            for principal in readers:
                repo.grant_pull(principal)
            CfnOutput(self, f"RepoUri-{name}", value=repo.repository_uri)
