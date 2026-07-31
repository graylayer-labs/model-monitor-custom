"""Tests for manifest trigger — EventBridge S3→SFN wiring."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions as cdka

from model_monitor_cdk.stacks.baseline_trigger_stack import BaselineTriggerStack


def test_baseline_trigger_stack_synth_ok():
    """BaselineTriggerStack should synth without errors."""
    app = cdk.App()
    stack = BaselineTriggerStack(
        app,
        "test-trigger",
        baselines_bucket_name="test-baselines",
        baseline_sfn_arn="arn:aws:states:eu-west-1:123456789012:stateMachine:baseline-sfn",
        environment="test",
    )
    assert stack is not None


def test_baseline_trigger_creates_eventbridge_rule():
    """BaselineTriggerStack should create EventBridge rule."""
    app = cdk.App()
    stack = BaselineTriggerStack(
        app,
        "test-trigger",
        baselines_bucket_name="test-baselines",
        baseline_sfn_arn="arn:aws:states:eu-west-1:123456789012:stateMachine:baseline-sfn",
        environment="test",
    )
    template = cdka.Template.from_stack(stack)
    template.resource_count_is("AWS::Events::Rule", 1)


def test_baseline_trigger_rule_matches_manifest_json():
    """EventBridge rule should match S3 PutObject with manifest.json suffix."""
    app = cdk.App()
    stack = BaselineTriggerStack(
        app,
        "test-trigger",
        baselines_bucket_name="test-baselines",
        baseline_sfn_arn="arn:aws:states:eu-west-1:123456789012:stateMachine:baseline-sfn",
        environment="test",
    )
    template = cdka.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "EventPattern": {
                "source": ["aws.s3"],
                "detail-type": ["Object Created"],
                "detail": {
                    "bucket": {
                        "name": ["test-baselines"],
                    },
                    "object": {
                        "key": [
                            {
                                "suffix": "manifest.json",
                            },
                        ],
                    },
                },
            },
        },
    )


def test_baseline_trigger_rule_targets_sfn():
    """EventBridge rule should target the Baseline SFN."""
    app = cdk.App()
    sfn_arn = "arn:aws:states:eu-west-1:123456789012:stateMachine:baseline-sfn"
    stack = BaselineTriggerStack(
        app,
        "test-trigger",
        baselines_bucket_name="test-baselines",
        baseline_sfn_arn=sfn_arn,
        environment="test",
    )
    template = cdka.Template.from_stack(stack)
    # Check that a target points to the SFN
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Targets": cdka.Match.array_with(
                [
                    cdka.Match.object_like(
                        {
                            "Arn": sfn_arn,
                        }
                    )
                ]
            )
        },
    )


def test_baseline_trigger_sfn_role_trusted_by_events():
    """EventBridge needs permission to invoke SFN."""
    app = cdk.App()
    stack = BaselineTriggerStack(
        app,
        "test-trigger",
        baselines_bucket_name="test-baselines",
        baseline_sfn_arn="arn:aws:states:eu-west-1:123456789012:stateMachine:baseline-sfn",
        environment="test",
    )
    template = cdka.Template.from_stack(stack)
    # Check that an IAM role exists with StartExecution permission
    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "AssumeRolePolicyDocument": cdka.Match.object_like(
                {
                    "Statement": cdka.Match.array_with(
                        [
                            cdka.Match.object_like(
                                {
                                    "Principal": {
                                        "Service": "events.amazonaws.com",
                                    },
                                }
                            )
                        ]
                    )
                }
            )
        },
    )
