"""Synth tests for InferenceMonitorStack."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template
from model_monitor_cdk.stacks.inference_monitor_stack import (
    InferenceMonitorStack,
    InferenceMonitorStackProps,
)

_SCHEDULER_EXPR_RE = re.compile(
    r"^(cron\(\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\)"
    r"|rate\(\d+\s+(minute|minutes|hour|hours|day|days)\)"
    r"|at\(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\))$",
)


def _is_valid_scheduler_expression(expr: str) -> bool:
    """Client-side EventBridge Scheduler expression syntax check.

    Scheduler cron takes 6 fields (minute hour day-of-month month day-of-week year),
    not the 5-field Unix cron. Rate accepts ``rate(N unit)`` and at accepts an ISO8601
    instant. See:
    https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html
    """
    return bool(_SCHEDULER_EXPR_RE.match(expr))


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
    template = _synth(_valid_props(compute_backend="ecs"))
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
    template = _synth(_valid_props(compute_backend="ecs"))
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


def test_scheduler_expression_regex_accepts_valid_and_rejects_invalid():
    """Validator itself: accept Scheduler-legal shapes, reject 5-field cron."""
    valid = [
        "cron(0 * * * ? *)",
        "cron(0 12 ? * MON-FRI *)",
        "rate(5 minutes)",
        "rate(1 hour)",
    ]
    invalid = [
        "cron(0 * * * *)",  # 5-field Unix cron — Scheduler rejects
        "cron(0 * * * ?)",  # 5 fields
        "rate(5 seconds)",  # seconds not supported
        "rate(minutes)",  # missing count
        "every 5 minutes",  # freeform
        "",
    ]
    for expr in valid:
        assert _is_valid_scheduler_expression(expr), f"expected valid: {expr!r}"
    for expr in invalid:
        assert not _is_valid_scheduler_expression(expr), f"expected invalid: {expr!r}"


def test_scheduler_schedule_expression_is_valid():
    """Rendered ScheduleExpression must be syntactically valid Scheduler cron/rate."""
    template = _synth()
    schedules = template.find_resources("AWS::Scheduler::Schedule")
    assert len(schedules) == 1
    expr = next(iter(schedules.values()))["Properties"]["ScheduleExpression"]
    assert _is_valid_scheduler_expression(expr), f"invalid Scheduler expression: {expr!r}"


def test_scheduler_rejects_five_field_cron_at_synth_time_via_validator():
    """Guard against the silent-typo class: 5-field Unix cron slipping through props."""
    props = _valid_props(schedule_expression="cron(0 * * * *)")
    app = App()
    stack = InferenceMonitorStack(
        app,
        "MMC-Test-InferenceMonitor-BadCron",
        props=props,
        env=Environment(account=_CONSUMER_ACCOUNT, region="eu-west-1"),
    )
    template = Template.from_stack(stack)
    expr = next(iter(template.find_resources("AWS::Scheduler::Schedule").values()))["Properties"]["ScheduleExpression"]
    assert not _is_valid_scheduler_expression(expr), (
        "Validator failed to catch 5-field Unix cron — Scheduler would silently reject at deploy."
    )


def test_task_role_policy_grants_kms_decrypt_on_artifact_key():
    """Each Fargate task role must be able to decrypt objects encrypted by the artifact KMS key."""
    template = _synth(_valid_props(compute_backend="ecs"))
    key_arn = f"arn:aws:kms:eu-west-1:{_ARTIFACT_ACCOUNT}:key/abcd1234"
    for analyser in _ANALYSERS:
        template.has_resource_properties(
            "AWS::IAM::Policy",
            {
                "PolicyDocument": {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Action": "kms:Decrypt",
                                    "Effect": "Allow",
                                    "Resource": key_arn,
                                },
                            ),
                        ],
                    ),
                },
                "Roles": Match.array_with(
                    [{"Ref": Match.string_like_regexp(f"TaskRole{analyser.title()}.*")}],
                ),
            },
        )


def _flatten_definition_for_parse(defn: Any) -> str:
    """Like ``_flatten_definition`` but substitutes CFN tokens with a placeholder token.

    The raw ``_flatten_definition`` dumps intrinsic dicts as JSON, which corrupts
    the surrounding string (they land inside a JSON string literal). For JSON
    parsing of the ASL definition, replace the intrinsic with a bare placeholder.
    """
    if isinstance(defn, str):
        return defn
    if isinstance(defn, dict):
        join = defn.get("Fn::Join")
        if join:
            _, parts = join
            return "".join(_flatten_definition_for_parse(p) for p in parts)
        return "__TOKEN__"
    if isinstance(defn, list):
        return "".join(_flatten_definition_for_parse(p) for p in defn)
    return str(defn)


def test_each_parallel_branch_catches_to_distinct_failure_state():
    """Per-branch Catch must route to its own failure state, not a shared terminal."""
    template = _synth()
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    raw = next(iter(sms.values()))["Properties"].get("DefinitionString") or next(iter(sms.values()))["Properties"].get(
        "DefinitionBody"
    )
    definition = json.loads(_flatten_definition_for_parse(raw))
    branches = definition["States"]["AllAnalysers"]["Branches"]
    assert len(branches) == 5
    catch_targets: list[str] = []
    for branch in branches:
        run_state_name = branch["StartAt"]
        run_state = branch["States"][run_state_name]
        catches = run_state["Catch"]
        assert len(catches) == 1, f"branch {run_state_name} should have exactly one Catch"
        target = catches[0]["Next"]
        assert target in branch["States"], f"Catch target {target!r} not in same branch"
        assert branch["States"][target]["Type"] == "Pass"
        catch_targets.append(target)
    assert len(set(catch_targets)) == 5, f"Catch targets not distinct: {catch_targets}"
    expected = {f"Branch{a.title()}Failed" for a in _ANALYSERS}
    assert set(catch_targets) == expected


def test_synth_ok_with_lambda_backend():
    """Lambda backend should synth without errors."""
    _synth(_valid_props(compute_backend="lambda"))


def test_lambda_backend_has_zero_ecs_resources():
    """Lambda backend should have no ECS task definitions or VPCs."""
    template = _synth(_valid_props(compute_backend="lambda"))
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    assert len(task_defs) == 0
    vpcs = template.find_resources("AWS::EC2::VPC")
    assert len(vpcs) == 0


def test_lambda_backend_has_five_lambda_functions():
    """Lambda backend should create 5 Lambda functions (one per analyser)."""
    template = _synth(_valid_props(compute_backend="lambda"))
    functions = template.find_resources("AWS::Lambda::Function")
    # Note: there may be other Lambdas (notifier, archive_writer, etc), so check
    # that at least the 5 analyser Lambdas exist by looking for the naming pattern
    names = {r["Properties"].get("FunctionName", "") for r in functions.values()}
    analyser_functions = {n for n in names if any(a in n for a in _ANALYSERS)}
    assert len(analyser_functions) >= 5, f"Expected ≥5 analyser Lambdas, got: {analyser_functions}"


def test_lambda_backend_state_machine_invokes_lambda():
    """Lambda backend state machine should invoke Lambda (not ECS RunTask)."""
    template = _synth(_valid_props(compute_backend="lambda"))
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    raw = next(iter(sms.values()))["Properties"].get("DefinitionString") or next(iter(sms.values()))["Properties"].get(
        "DefinitionBody"
    )
    text = _flatten_definition(raw)
    assert "lambda:invoke" in text
    # Each Lambda task has 1 Retry + Parallel has 1 Retry = at least 5 (one per branch)
    assert text.count('"Retry"') >= 5
    assert text.count('"Catch"') == 5


def test_ecs_backend_still_works():
    """ECS backend should still produce exactly the current ECS shape (regression)."""
    template = _synth(_valid_props(compute_backend="ecs"))
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    assert len(task_defs) == 5
    families = sorted(r["Properties"]["Family"] for r in task_defs.values())
    assert families == sorted(f"mmc-test-{a}" for a in _ANALYSERS)
    # State machine should use ecs:runTask, not lambda:invoke
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    raw = next(iter(sms.values()))["Properties"].get("DefinitionString") or next(iter(sms.values()))["Properties"].get(
        "DefinitionBody"
    )
    text = _flatten_definition(raw)
    assert "ecs:runTask" in text
    assert "lambda:invoke" not in text
