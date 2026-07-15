"""Synth tests for ProducerEventsStack."""

from __future__ import annotations

import json
from typing import Any

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template
from model_monitor_cdk.stacks.producer_events_stack import (
    ProducerEventsStack,
    ProducerEventsStackProps,
)

_PRODUCER_ACCOUNT = "111111111111"
_OPERATIONS_ACCOUNT = "222222222222"
_REGION = "eu-west-1"
_BUCKET = "producer-training-snapshots"
_BUCKET_ARN = f"arn:aws:s3:::{_BUCKET}"
_PREFIX = "training-snapshots/"


def _valid_props(**overrides: Any) -> ProducerEventsStackProps:
    kwargs: dict[str, Any] = {
        "environment": "test",
        "project_name": "example-classifier",
        "producer_bucket_arn": _BUCKET_ARN,
        "producer_prefix": _PREFIX,
        "operations_account_id": _OPERATIONS_ACCOUNT,
        "operations_region": _REGION,
    }
    kwargs.update(overrides)
    return ProducerEventsStackProps(**kwargs)


def _synth(props: ProducerEventsStackProps | None = None) -> Template:
    app = App()
    stack = ProducerEventsStack(
        app,
        "MMC-Test-ProducerEvents-Example",
        props=props or _valid_props(),
        env=Environment(account=_PRODUCER_ACCOUNT, region=_REGION),
    )
    return Template.from_stack(stack)


def test_synth_ok():
    _synth()


def test_forward_rule_pattern_matches_bucket_and_prefix():
    template = _synth()
    rules = template.find_resources("AWS::Events::Rule")
    assert len(rules) == 1
    pattern = next(iter(rules.values()))["Properties"]["EventPattern"]
    assert pattern["source"] == ["aws.s3"]
    assert pattern["detail-type"] == ["Object Created"]
    assert pattern["detail"]["bucket"]["name"] == [_BUCKET]
    assert pattern["detail"]["object"]["key"] == [{"prefix": _PREFIX}]


def test_rule_target_is_operations_default_bus_arn():
    template = _synth()
    rules = template.find_resources("AWS::Events::Rule")
    targets = next(iter(rules.values()))["Properties"]["Targets"]
    assert len(targets) == 1
    target_arn = targets[0]["Arn"]
    expected = f"arn:aws:events:{_REGION}:{_OPERATIONS_ACCOUNT}:event-bus/default"
    assert target_arn == expected


def test_forwarder_role_grants_put_events_to_target_bus():
    template = _synth()
    expected_arn = f"arn:aws:events:{_REGION}:{_OPERATIONS_ACCOUNT}:event-bus/default"
    rendered = json.dumps(
        [p["Properties"]["PolicyDocument"] for p in template.find_resources("AWS::IAM::Policy").values()],
    )
    assert "events:PutEvents" in rendered
    assert expected_arn in rendered


def test_forwarder_role_trust_is_events_service():
    template = _synth()
    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "AssumeRolePolicyDocument": Match.object_like(
                {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Effect": "Allow",
                                    "Principal": {"Service": "events.amazonaws.com"},
                                    "Action": "sts:AssumeRole",
                                },
                            ),
                        ],
                    ),
                },
            ),
        },
    )


def test_outputs_present():
    template = _synth()
    for output in ["ForwardRuleArn", "TargetBusArn"]:
        template.has_output(output, Match.any_value())


def test_bad_operations_account_rejected():
    with pytest.raises(ValueError, match="operations_account_id"):
        _valid_props(operations_account_id="12")


def test_bad_bucket_arn_rejected():
    with pytest.raises(ValueError, match="producer_bucket_arn"):
        _valid_props(producer_bucket_arn="not-an-arn")


def test_empty_prefix_rejected():
    with pytest.raises(ValueError, match="producer_prefix"):
        _valid_props(producer_prefix="")


def test_empty_project_rejected():
    with pytest.raises(ValueError, match="project_name"):
        _valid_props(project_name="")
