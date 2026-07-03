"""SharedIamStack — cross-account baseline reader/writer roles.

Lives in the ``ml-artifact`` account. Per ADR 008, mints one writer role
trusted by ``ml-operations`` plus one reader role per consumer account in
``reader_accounts``. Never reads ``self.account`` / ``self.region`` — all
identity flows in via :class:`SharedIamStackProps`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from aws_cdk import CfnOutput, Duration, Environment, Stack, Tags
from aws_cdk import aws_iam as iam

if TYPE_CHECKING:
    from constructs import Construct

_ACCOUNT_ID_PATTERN = re.compile(r"^\d{12}$")
_VALID_ENVIRONMENTS = ("test", "prod", "dev")


@dataclass(frozen=True, kw_only=True)
class SharedIamStackProps:
    """Configuration for :class:`SharedIamStack`.

    Attributes:
        environment: Deployment environment tag (``test``, ``prod``, ``dev``).
        reader_accounts: 1:N consumer account IDs that need read access to
            the baselines bucket + KMS key. Each gets its own role.
        writer_account_id: ``ml-operations`` account ID; sole writer principal.
        baselines_bucket_arn: ARN of the baselines S3 bucket (ArtifactStack output).
        artifact_kms_key_arn: ARN of the artifact KMS CMK (ArtifactStack output).
        baseline_prefix: S3 prefix under the bucket that reader/writer policies
            scope to. Trailing slash preserved.
    """

    environment: Literal["test", "prod", "dev"]
    reader_accounts: list[str]
    writer_account_id: str
    baselines_bucket_arn: str
    artifact_kms_key_arn: str
    baseline_prefix: str = "baselines/"

    def __post_init__(self) -> None:
        """Validate props on construction.

        Raises:
            ValueError: If any field fails validation.
        """
        if self.environment not in _VALID_ENVIRONMENTS:
            msg = f"environment must be one of {_VALID_ENVIRONMENTS}, got: {self.environment!r}"
            raise ValueError(msg)
        if not self.reader_accounts:
            msg = "reader_accounts must contain at least one account ID"
            raise ValueError(msg)
        for account_id in self.reader_accounts:
            if not _ACCOUNT_ID_PATTERN.match(account_id):
                msg = f"reader_accounts entries must be 12 digits, got: {account_id!r}"
                raise ValueError(msg)
        if len(set(self.reader_accounts)) != len(self.reader_accounts):
            msg = f"reader_accounts must not contain duplicates, got: {self.reader_accounts!r}"
            raise ValueError(msg)
        if not _ACCOUNT_ID_PATTERN.match(self.writer_account_id):
            msg = f"writer_account_id must be 12 digits, got: {self.writer_account_id!r}"
            raise ValueError(msg)
        if not self.baselines_bucket_arn:
            msg = "baselines_bucket_arn must be non-empty"
            raise ValueError(msg)
        if not self.artifact_kms_key_arn:
            msg = "artifact_kms_key_arn must be non-empty"
            raise ValueError(msg)
        if not self.baseline_prefix:
            msg = "baseline_prefix must be non-empty"
            raise ValueError(msg)


class SharedIamStack(Stack):
    """Cross-account baseline reader/writer IAM roles.

    Per ADR 008 — one writer role, N reader roles (one per consumer). Scoped
    to a passed-in baselines bucket ARN and KMS key ARN so no ambient account
    or region identity leaks in.

    Attributes:
        reader_role_arns: Mapping of consumer account ID → reader role ARN,
            for in-app cross-stack refs when both stacks share one ``App``.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: SharedIamStackProps,
        env: Environment | None = None,
    ) -> None:
        """Construct the stack.

        Args:
            scope: Parent construct (the CDK ``App`` or a nested stage).
            construct_id: Logical ID for the stack.
            props: Validated :class:`SharedIamStackProps`.
            env: CDK ``Environment`` (account + region). Optional.
        """
        super().__init__(scope, construct_id, env=env)
        self._props = props
        self.reader_role_arns: dict[str, str] = {}

        writer_role = self._build_writer_role()
        Tags.of(writer_role).add("mmc:role", "baseline-writer")
        Tags.of(writer_role).add("mmc:environment", props.environment)
        CfnOutput(self, "BaselineWriterRoleArn", value=writer_role.role_arn)

        for account_id in props.reader_accounts:
            reader = self._build_reader_role(account_id)
            Tags.of(reader).add("mmc:role", "baseline-reader")
            Tags.of(reader).add("mmc:environment", props.environment)
            Tags.of(reader).add("mmc:reader-account", account_id)
            self.reader_role_arns[account_id] = reader.role_arn
            CfnOutput(
                self,
                f"BaselineReaderRoleArn-{account_id}",
                value=reader.role_arn,
            )

    def _prefix_object_arn(self) -> str:
        return f"{self._props.baselines_bucket_arn}/{self._props.baseline_prefix}*"

    def _list_bucket_condition(self) -> dict[str, dict[str, list[str]]]:
        return {"StringLike": {"s3:prefix": [f"{self._props.baseline_prefix}*"]}}

    def _build_writer_role(self) -> iam.Role:
        return iam.Role(
            self,
            "BaselineWriter",
            assumed_by=iam.AccountPrincipal(self._props.writer_account_id),  # ty: ignore[invalid-argument-type]
            max_session_duration=Duration.hours(1),
            inline_policies={
                "BaselineWriterPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="BaselineWrite",
                            actions=["s3:PutObject", "s3:GetObject"],
                            resources=[self._prefix_object_arn()],
                        ),
                        iam.PolicyStatement(
                            sid="BaselineList",
                            actions=["s3:ListBucket"],
                            resources=[self._props.baselines_bucket_arn],
                            conditions=self._list_bucket_condition(),
                        ),
                        iam.PolicyStatement(
                            sid="KmsWrite",
                            actions=["kms:GenerateDataKey", "kms:Decrypt"],
                            resources=[self._props.artifact_kms_key_arn],
                        ),
                    ],
                ),
            },
        )

    def _build_reader_role(self, account_id: str) -> iam.Role:
        return iam.Role(
            self,
            f"BaselineReader-{account_id}",
            assumed_by=iam.AccountPrincipal(account_id),  # ty: ignore[invalid-argument-type]
            max_session_duration=Duration.hours(1),
            inline_policies={
                "BaselineReaderPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="BaselineRead",
                            actions=["s3:GetObject"],
                            resources=[self._prefix_object_arn()],
                        ),
                        iam.PolicyStatement(
                            sid="BaselineList",
                            actions=["s3:ListBucket"],
                            resources=[self._props.baselines_bucket_arn],
                            conditions=self._list_bucket_condition(),
                        ),
                        iam.PolicyStatement(
                            sid="KmsRead",
                            actions=["kms:Decrypt"],
                            resources=[self._props.artifact_kms_key_arn],
                        ),
                    ],
                ),
            },
        )
