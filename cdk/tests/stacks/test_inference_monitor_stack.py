"""Synth tests for InferenceMonitorStack."""

from __future__ import annotations

import json
from typing import Any

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template
from model_monitor_cdk.stacks.inference_monitor_stack import (
    InferenceMonitorStack,
    InferenceMonitorStackProps,
)

_ANALYSERS = ("mq", "dq", "bias", "explain", "shadow")
_CONSUMER_ACCOUNT = "111111111111"
_ARTIFACT_ACCOUNT = "222222222222"


def _image_uris() -> dict[str, str]:
    return {
        analyser: f"333333333333.dkr.ecr.eu-west-1.amazonaws.com/mmc/analyser-{analyser}:sha-abc"
        for analyser in _ANALYSERS
    }


def _valid_props(**overrides: Any) -> InferenceMonitorStackProps:
    kwargs: dict[str, Any] = {
        "environment": "test",
        "project_name": "example-classifier",
        "consumer_account_id": _CONSUMER_ACCOUNT,
        "artifact_account_id": _ARTIFACT_ACCOUNT,
        "artifact_kms_key_arn": f"arn:aws:kms:eu-west-1:{_ARTIFACT_ACCOUNT}:key/abcd1234",
        "baselines_bucket_arn": "arn:aws:s3:::mmc-baselines",
        "analyser_image_uris": _image_uris(),
    }
    kwargs.update(overrides)
    return InferenceMonitorStackProps(**kwargs)


def _synth(props: InferenceMonitorStackProps | None = None) -> Template:
    app = App()
    stack = InferenceMonitorStack(
        app,
        "MMC-Test-InferenceMonitor-Example",
        props=props or _valid_props(),
        env=Environment(account=_CONSUMER_ACCOUNT, region="eu-west-1"),
    )
    return Template.from_stack(stack)


def test_synth_ok():
    _synth()


def test_ddb_table_has_new_image_stream():
    template = _synth()
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {"StreamSpecification": {"StreamViewType": "NEW_IMAGE"}},
    )


def _flatten_definition(defn: Any) -> str:
    """Turn a CFN DefinitionString token (Fn::Join / dict) into a plain string."""
    if isinstance(defn, str):
        return defn
    if isinstance(defn, dict):
        join = defn.get("Fn::Join")
        if join:
            _, parts = join
            return "".join(_flatten_definition(p) for p in parts)
        return json.dumps(defn)
    if isinstance(defn, list):
        return "".join(_flatten_definition(p) for p in defn)
    return str(defn)


def test_state_machine_parallel_with_five_branches():
    template = _synth()
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    assert len(sms) == 1
    raw = next(iter(sms.values()))["Properties"].get("DefinitionString") or next(iter(sms.values()))["Properties"].get(
        "DefinitionBody"
    )
    text = _flatten_definition(raw)
    assert "Parallel" in text
    # count branches by counting Retry / Catch occurrences on run states
    assert text.count('"Retry"') == 5
    assert text.count('"Catch"') == 5


def test_five_task_definitions_one_per_analyser():
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    assert len(task_defs) == 5
    families = sorted(r["Properties"]["Family"] for r in task_defs.values())
    assert families == sorted(f"mmc-test-{a}" for a in _ANALYSERS)


def test_two_pipes_notifier_and_archive():
    template = _synth()
    pipes = template.find_resources("AWS::Pipes::Pipe")
    assert len(pipes) == 2
    names = {r["Properties"]["Name"] for r in pipes.values()}
    assert names == {"mmc-test-outcome-notifier", "mmc-test-outcome-archive"}


def test_notifier_pipe_has_severity_alert_filter():
    template = _synth()
    pipes = template.find_resources("AWS::Pipes::Pipe")
    notifier = next(r for r in pipes.values() if r["Properties"]["Name"] == "mmc-test-outcome-notifier")
    filters = notifier["Properties"]["SourceParameters"]["FilterCriteria"]["Filters"]
    assert len(filters) == 1
    pattern = filters[0]["Pattern"]
    assert '"severity"' in pattern
    assert '"S":["alert"]' in pattern.replace(" ", "")


def test_archive_pipe_has_no_filter():
    template = _synth()
    pipes = template.find_resources("AWS::Pipes::Pipe")
    archive = next(r for r in pipes.values() if r["Properties"]["Name"] == "mmc-test-outcome-archive")
    source = archive["Properties"]["SourceParameters"]
    assert "FilterCriteria" not in source or not source.get("FilterCriteria", {}).get("Filters")


def test_log_groups_per_analyser_with_retention():
    template = _synth()
    for analyser in _ANALYSERS:
        template.has_resource_properties(
            "AWS::Logs::LogGroup",
            {
                "LogGroupName": f"/mmc/test/{analyser}",
                "RetentionInDays": 30,
            },
        )


def test_bad_ecr_uri_rejected():
    with pytest.raises(ValueError, match="analyser_image_uris"):
        bad = _image_uris()
        bad["mq"] = "not-an-ecr-uri"
        _valid_props(analyser_image_uris=bad)


def test_missing_analyser_rejected():
    with pytest.raises(ValueError, match="missing keys"):
        missing = _image_uris()
        del missing["shadow"]
        _valid_props(analyser_image_uris=missing)


def test_non_12_digit_consumer_account_rejected():
    with pytest.raises(ValueError, match="consumer_account_id"):
        _valid_props(consumer_account_id="12")


def test_no_hardcoded_account_ids_in_source():
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "model_monitor_cdk" / "stacks" / "inference_monitor_stack.py"
    body = src.read_text()
    import re

    hits = [m for m in re.findall(r"\b\d{12}\b", body) if m not in {"123456789012"}]
    assert not hits, f"Hardcoded 12-digit account IDs in source: {hits}"


def test_outputs_present():
    template = _synth()
    for output in [
        "StateMachineArn",
        "OutcomesTableName",
        "OutcomesStreamArn",
        "NotifierLambdaArn",
        "ArchiveBucketName",
        "NotifierPipeName",
        "ArchivePipeName",
    ]:
        template.has_output(output, Match.any_value())


def test_archive_bucket_created():
    template = _synth()
    buckets = template.find_resources("AWS::S3::Bucket")
    assert len(buckets) >= 1
