"""ConfigStack — S3 bucket for versioned project configs."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3


class ConfigStack(cdk.Stack):
    """Stack for versioned project configuration bucket.

    Creates an S3 bucket with versioning and KMS encryption for storing
    YAML-derived project configs. Each project has a versioned key:
    s3://config-bucket/<project>/v<N>/config.json
    """

    def __init__(
        self,
        scope: cdk.App | cdk.Stack,
        construct_id: str,
        *,
        region: str,
        environment: str,
        **kwargs,
    ):
        """Initialize ConfigStack.

        Args:
            scope: CDK parent.
            construct_id: Stack ID.
            region: AWS region.
            environment: Environment tag (test/dev/prod).
            **kwargs: Additional Stack kwargs.
        """
        super().__init__(scope, construct_id, **kwargs)

        # KMS key for bucket encryption
        kms_key = kms.Key(
            self,
            "ConfigBucketKey",
            enable_key_rotation=True,
            description=f"mmc-config-{environment} bucket encryption key",
        )
        kms.Alias(
            self,
            "ConfigBucketKeyAlias",
            alias_name=f"alias/mmc-config-{environment}",
            target_key=kms_key,
        )

        # Versioned config bucket
        bucket = s3.Bucket(
            self,
            "ConfigBucket",
            bucket_name=f"mmc-config-{environment}-{self.account}-{region}",
            versioned=True,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=kms_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    noncurrent_version_expiration=cdk.Duration.days(90),
                ),
            ],
        )

        # Output
        cdk.CfnOutput(
            self,
            "ConfigBucketArn",
            value=bucket.bucket_arn,
            description="Config bucket ARN",
            export_name=f"{self.stack_name}-bucket-arn",
        )
        cdk.CfnOutput(
            self,
            "ConfigBucketName",
            value=bucket.bucket_name,
            description="Config bucket name",
            export_name=f"{self.stack_name}-bucket-name",
        )

        self.config_bucket = bucket
