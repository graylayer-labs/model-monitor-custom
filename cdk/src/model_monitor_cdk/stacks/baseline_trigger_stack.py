"""BaselineTriggerStack — EventBridge rule for S3 manifest.json → Baseline SFN."""

from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk import aws_events as events
from aws_cdk import aws_iam as iam


class BaselineTriggerStack(cdk.Stack):
    """Stack for EventBridge rule triggering Baseline SFN on manifest.json arrival."""

    def __init__(
        self,
        scope: cdk.App | cdk.Stack,
        construct_id: str,
        *,
        baselines_bucket_name: str,
        baseline_sfn_arn: str,
        environment: str,
        **kwargs,
    ):
        """Initialize BaselineTriggerStack."""
        super().__init__(scope, construct_id, **kwargs)

        # IAM role for EventBridge to invoke SFN
        event_role = iam.Role(
            self,
            "BaselineTriggerRole",
            assumed_by=iam.ServicePrincipal("events.amazonaws.com"),
        )
        event_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["states:StartExecution"],
                resources=[baseline_sfn_arn],
            )
        )

        # EventBridge rule: S3 PutObject with manifest.json suffix
        self.rule = events.Rule(
            self,
            "ManifestArrival",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [baselines_bucket_name]},
                    "object": {"key": [{"suffix": "manifest.json"}]},
                },
            ),
        )

        # Add SFN as target via CfnRule (for raw EventBridge configuration)
        cfn_rule = self.rule.node.default_child
        cfn_rule.add_property_override(
            "Targets",
            [
                {
                    "Arn": baseline_sfn_arn,
                    "RoleArn": event_role.role_arn,
                    "Input": json.dumps({"detail.$": "$.detail"}),
                }
            ],
        )
