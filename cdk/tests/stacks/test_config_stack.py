"""Tests for ConfigStack — versioned project config S3 bucket."""

from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk import assertions as cdka

from model_monitor_cdk.stacks.config_stack import ConfigStack


def test_config_stack_synth_ok():
    """ConfigStack should synth without errors."""
    app = cdk.App()
    stack = ConfigStack(
        app,
        "test-config",
        region="eu-west-1",
        environment="test",
    )
    assert stack is not None


def test_config_stack_creates_s3_bucket():
    """ConfigStack should create an S3 bucket for versioned configs."""
    app = cdk.App()
    stack = ConfigStack(
        app,
        "test-config",
        region="eu-west-1",
        environment="test",
    )
    template = cdka.Template.from_stack(stack)
    template.resource_count_is("AWS::S3::Bucket", 1)


def test_config_stack_bucket_has_versioning():
    """Config bucket should have versioning enabled."""
    app = cdk.App()
    stack = ConfigStack(
        app,
        "test-config",
        region="eu-west-1",
        environment="test",
    )
    template = cdka.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "VersioningConfiguration": {
                "Status": "Enabled",
            },
        },
    )


def test_config_stack_bucket_has_kms_encryption():
    """Config bucket should have KMS encryption enabled."""
    app = cdk.App()
    stack = ConfigStack(
        app,
        "test-config",
        region="eu-west-1",
        environment="test",
    )
    template = cdka.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {
                        "ServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                        },
                    },
                ],
            },
        },
    )


def test_config_stack_bucket_blocks_public_access():
    """Config bucket should block all public access."""
    app = cdk.App()
    stack = ConfigStack(
        app,
        "test-config",
        region="eu-west-1",
        environment="test",
    )
    template = cdka.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        },
    )


def test_config_stack_outputs_bucket_arn():
    """ConfigStack should output bucket ARN."""
    app = cdk.App()
    stack = ConfigStack(
        app,
        "test-config",
        region="eu-west-1",
        environment="test",
    )
    template = cdka.Template.from_stack(stack)
    template.has_output("ConfigBucketArn", {})


def test_config_stack_bucket_name_is_deterministic():
    """Bucket name pattern should be deterministic for the environment."""
    app = cdk.App()
    stack = ConfigStack(
        app,
        "test-config",
        region="eu-west-1",
        environment="prod",
    )
    template = cdka.Template.from_stack(stack)

    # Check bucket exists with BucketName property set
    buckets = template.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 1
    bucket_props = list(buckets.values())[0]["Properties"]
    # BucketName is set to a deterministic value based on environment
    assert "BucketName" in bucket_props
