"""ProducerEventsStack — cross-account S3 event forwarder.

Deployed in the account that owns the producer bucket when it differs from
the operations account. Creates an EventBridge rule on the producer's
default bus that matches S3 ``Object Created`` events on the configured
bucket + prefix and forwards them to the operations account's default bus.

See ADR 010 (cross-account events) — this is the producer-side half of
the topology; :class:`OperationsBaselineStack` provisions the receiver-side
bus policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from aws_cdk import CfnOutput, Stack, Tags
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as events_targets,
)
from aws_cdk import (
    aws_iam as iam,
)
from constructs import Construct

_ACCOUNT_ID_PATTERN = re.compile(r"^\d{12}$")
_S3_BUCKET_ARN_PATTERN = re.compile(r"^arn:aws:s3:::[a-z0-9][a-z0-9.-]*[a-z0-9]$")


def _bucket_name_from_arn(bucket_arn: str) -> str:
    """Extract the bucket name from an ``arn:aws:s3:::<bucket>`` ARN.

    Args:
        bucket_arn: S3 bucket ARN.

    Returns:
        The bucket name.

    Raises:
        ValueError: If the ARN is malformed.
    """
    prefix = "arn:aws:s3:::"
    if not bucket_arn.startswith(prefix):
        msg = f"expected an s3 bucket ARN, got: {bucket_arn!r}"
        raise ValueError(msg)
    return bucket_arn[len(prefix) :].split("/", 1)[0]


@dataclass(frozen=True, kw_only=True)
class ProducerEventsStackProps:
    """Configuration for :class:`ProducerEventsStack`.

    Attributes:
        environment: Deployment environment tag.
        project_name: Project slug (used in construct + rule naming).
        producer_bucket_arn: ARN of the bucket to watch (must be in this
            stack's account).
        producer_prefix: Object key prefix filter.
        operations_account_id: 12-digit ID of the account receiving events.
        operations_region: AWS region of the operations default bus.
    """

    environment: str
    project_name: str
    producer_bucket_arn: str
    producer_prefix: str
    operations_account_id: str
    operations_region: str

    def __post_init__(self) -> None:
        """Validate props at construction.

        Raises:
            ValueError: If any identifier or ARN is malformed.
        """
        if not self.project_name:
            msg = "project_name must be non-empty"
            raise ValueError(msg)
        if not _S3_BUCKET_ARN_PATTERN.match(self.producer_bucket_arn):
            msg = f"producer_bucket_arn must be an S3 bucket ARN, got: {self.producer_bucket_arn!r}"
            raise ValueError(msg)
        if not self.producer_prefix:
            msg = "producer_prefix must be non-empty"
            raise ValueError(msg)
        if not _ACCOUNT_ID_PATTERN.match(self.operations_account_id):
            msg = f"operations_account_id must be 12 digits, got: {self.operations_account_id!r}"
            raise ValueError(msg)
        if not self.operations_region:
            msg = "operations_region must be non-empty"
            raise ValueError(msg)


class ProducerEventsStack(Stack):
    """Forwards S3 Object Created events to a remote-account default bus."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: ProducerEventsStackProps,
        **kwargs: Any,  # ruff: ignore[any-type]
    ) -> None:
        """Wire the forwarding rule.

        Args:
            scope: Parent construct.
            construct_id: CDK construct id.
            props: Validated configuration.
            **kwargs: Passed through to :class:`aws_cdk.Stack`.
        """
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("Component", "producer-events")
        Tags.of(self).add("Project", props.project_name)
        Tags.of(self).add("Environment", props.environment)

        target_bus_arn = f"arn:aws:events:{props.operations_region}:{props.operations_account_id}:event-bus/default"
        target_bus = events.EventBus.from_event_bus_arn(self, "OperationsDefaultBus", target_bus_arn)

        forwarder_role = iam.Role(
            self,
            "ForwarderRole",
            assumed_by=iam.ServicePrincipal("events.amazonaws.com"),  # ty: ignore[invalid-argument-type]
            description=(f"Forwards S3 Object Created events for {props.project_name} to the operations bus"),
        )
        forwarder_role.add_to_policy(
            iam.PolicyStatement(
                actions=["events:PutEvents"],
                resources=[target_bus_arn],
            ),
        )

        bucket_name = _bucket_name_from_arn(props.producer_bucket_arn)
        rule = events.Rule(
            self,
            "ForwardObjectCreatedRule",
            rule_name=f"mmc-{props.environment}-{props.project_name}-producer-forward",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [bucket_name]},
                    "object": {"key": [{"prefix": props.producer_prefix}]},
                },
            ),
        )
        rule.add_target(
            events_targets.EventBus(target_bus, role=forwarder_role),  # ty: ignore[invalid-argument-type]
        )

        CfnOutput(self, "ForwardRuleArn", value=rule.rule_arn)
        CfnOutput(self, "TargetBusArn", value=target_bus_arn)
