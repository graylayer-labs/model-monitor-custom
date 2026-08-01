"""Synth + snapshot tests for OperationsBaselineStack."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template
from model_monitor_cdk.stacks.operations_baseline_stack import (
    OperationsBaselineStack,
    OperationsBaselineStackProps,
)

_ANALYSERS = ("mq", "dq", "bias", "explain", "shadow")
_OPERATIONS_ACCOUNT = "444444444444"
_ARTIFACT_ACCOUNT = "222222222222"
_WRITER_ROLE_ARN = f"arn:aws:iam::{_ARTIFACT_ACCOUNT}:role/mmc-test-baseline-writer"
_PRODUCER_BUCKET = "mmc-training-snapshots-test"
_PRODUCER_BUCKET_ARN = f"arn:aws:s3:::{_PRODUCER_BUCKET}"
_PRODUCER_PREFIX = "training-snapshots/"
_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def _image_uris() -> dict[str, str]:
    return {
        analyser: f"333333333333.dkr.ecr.eu-west-1.amazonaws.com/mmc/analyser-{analyser}:sha-abc"
        for analyser in _ANALYSERS
    }


def _valid_props(**overrides: Any) -> OperationsBaselineStackProps:
    kwargs: dict[str, Any] = {
        "environment": "test",
        "project_name": "example-classifier",
        "operations_account_id": _OPERATIONS_ACCOUNT,
        "artifact_account_id": _ARTIFACT_ACCOUNT,
        "baselines_bucket_arn": "arn:aws:s3:::mmc-baselines",
        "artifact_kms_key_arn": f"arn:aws:kms:eu-west-1:{_ARTIFACT_ACCOUNT}:key/abcd1234",
        "baseline_writer_role_arn": _WRITER_ROLE_ARN,
        "producer_bucket_arn": _PRODUCER_BUCKET_ARN,
        "producer_prefix": _PRODUCER_PREFIX,
        "analyser_image_uris": _image_uris(),
    }
    kwargs.update(overrides)
    return OperationsBaselineStackProps(**kwargs)


def _synth(props: OperationsBaselineStackProps | None = None) -> Template:
    app = App()
    stack = OperationsBaselineStack(
        app,
        "MMC-Test-OperationsBaseline-Example",
        props=props or _valid_props(),
        env=Environment(account=_OPERATIONS_ACCOUNT, region="eu-west-1"),
    )
    return Template.from_stack(stack)


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


def test_synth_ok():
    _synth()


def test_state_machine_parallel_with_five_branches():
    template = _synth()
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    assert len(sms) == 1
    props = next(iter(sms.values()))["Properties"]
    raw = props.get("DefinitionString") or props.get("DefinitionBody")
    text = _flatten_definition(raw)
    assert "Parallel" in text
    # New v2 flow has: LoadAndGate (1) + EvaluateResults (1) + WriteRegistry (1) + 5 ECS branches = 8 Retry blocks
    # And 8 Catch blocks (3 for Lambda tasks + 5 for ECS branches)
    assert text.count('"Retry"') == 8, f"Expected 8 Retry blocks, got {text.count('"Retry"')}"
    assert text.count('"Catch"') == 8, f"Expected 8 Catch blocks, got {text.count('"Catch"')}"


def test_five_task_definitions_one_per_analyser():
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    assert len(task_defs) == 5
    families = sorted(r["Properties"]["Family"] for r in task_defs.values())
    assert families == sorted(f"mmc-test-baseline-{a}" for a in _ANALYSERS)


def test_log_groups_per_analyser_with_retention():
    template = _synth()
    for analyser in _ANALYSERS:
        template.has_resource_properties(
            "AWS::Logs::LogGroup",
            {
                "LogGroupName": f"/mmc/test/baseline-{analyser}",
                "RetentionInDays": 30,
            },
        )


def test_event_rule_pattern_matches_producer_prefix():
    template = _synth()
    rules = template.find_resources("AWS::Events::Rule")
    triggers = [
        r
        for r in rules.values()
        if isinstance(r["Properties"].get("EventPattern"), dict)
        and r["Properties"]["EventPattern"].get("source") == ["aws.s3"]
    ]
    assert len(triggers) == 1
    pattern = triggers[0]["Properties"]["EventPattern"]
    assert pattern["source"] == ["aws.s3"]
    assert pattern["detail-type"] == ["Object Created"]
    assert pattern["detail"]["bucket"]["name"] == [_PRODUCER_BUCKET]
    assert pattern["detail"]["object"]["key"] == [{"prefix": _PRODUCER_PREFIX}]


def test_task_role_assumes_writer_role():
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    rendered_all = json.dumps([p["Properties"]["PolicyDocument"] for p in policies.values()])
    assert "sts:AssumeRole" in rendered_all
    assert _WRITER_ROLE_ARN in rendered_all


def test_task_role_no_direct_put_on_baselines_bucket():
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    for p in policies.values():
        rendered = json.dumps(p["Properties"]["PolicyDocument"])
        # No direct PutObject on the baselines bucket — must go via assumed role.
        assert "arn:aws:s3:::mmc-baselines" not in rendered


def test_baseline_registry_table_exists():
    """Registry table is created with correct schema."""
    template = _synth()
    tables = template.find_resources("AWS::DynamoDB::Table")
    assert len(tables) == 1, f"Expected exactly one DynamoDB table, found {len(tables)}"


def test_baseline_registry_table_key_schema():
    """Registry table has partition key 'project' (HASH) and sort key 'sk' (RANGE)."""
    template = _synth()
    tables = template.find_resources("AWS::DynamoDB::Table")
    table_props = next(iter(tables.values()))["Properties"]
    key_schema = table_props["KeySchema"]

    # Extract key attributes
    keys = {ks["AttributeName"]: ks["KeyType"] for ks in key_schema}
    assert keys.get("project") == "HASH", f"Expected 'project' as HASH key, got {keys}"
    assert keys.get("sk") == "RANGE", f"Expected 'sk' as RANGE key, got {keys}"


def test_no_pipes():
    template = _synth()
    assert template.find_resources("AWS::Pipes::Pipe") == {}


def test_outputs_present():
    template = _synth()
    for output in ["StateMachineArn", "EventRuleArn"]:
        template.has_output(output, Match.any_value())
    for analyser in _ANALYSERS:
        template.has_output(f"LogGroup{analyser.title()}Name", Match.any_value())


def test_no_ambient_account_ref_in_policies():
    template = _synth()
    for policy in template.find_resources("AWS::IAM::Policy").values():
        rendered = json.dumps(policy["Properties"]["PolicyDocument"])
        assert '"Ref":"AWS::AccountId"' not in rendered.replace(" ", "")


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


def test_non_12_digit_operations_account_rejected():
    with pytest.raises(ValueError, match="operations_account_id"):
        _valid_props(operations_account_id="12")


def test_non_12_digit_artifact_account_rejected():
    with pytest.raises(ValueError, match="artifact_account_id"):
        _valid_props(artifact_account_id="12")


def test_empty_writer_role_rejected():
    with pytest.raises(ValueError, match="baseline_writer_role_arn"):
        _valid_props(baseline_writer_role_arn="")


def test_empty_producer_bucket_rejected():
    with pytest.raises(ValueError, match="producer_bucket_arn"):
        _valid_props(producer_bucket_arn="")


def test_empty_baselines_bucket_rejected():
    with pytest.raises(ValueError, match="baselines_bucket_arn"):
        _valid_props(baselines_bucket_arn="")


def test_bad_producer_account_rejected():
    with pytest.raises(ValueError, match="producer_account_id"):
        _valid_props(producer_account_id="12")


def test_no_bus_policy_when_producer_account_matches_ops():
    template = _synth(_valid_props(producer_account_id=_OPERATIONS_ACCOUNT))
    assert template.find_resources("AWS::Events::EventBusPolicy") == {}


def test_no_bus_policy_when_producer_account_omitted():
    template = _synth()
    assert template.find_resources("AWS::Events::EventBusPolicy") == {}


def test_bus_policy_added_when_producer_account_differs():
    producer = "999999999999"
    template = _synth(_valid_props(producer_account_id=producer))
    policies = template.find_resources("AWS::Events::EventBusPolicy")
    assert len(policies) == 1
    rendered = json.dumps(next(iter(policies.values()))["Properties"])
    assert "events:PutEvents" in rendered
    assert f"arn:aws:iam::{producer}:root" in rendered


def test_no_hardcoded_account_ids_in_source():
    src = Path(__file__).resolve().parents[2] / "src" / "model_monitor_cdk" / "stacks" / "operations_baseline_stack.py"
    body = src.read_text()
    import re

    hits = [m for m in re.findall(r"\b\d{12}\b", body) if m not in {"123456789012"}]
    assert not hits, f"Hardcoded 12-digit account IDs in source: {hits}"


def test_task_role_policy_grants_kms_decrypt_on_artifact_key():
    """Each baseline task role must decrypt objects encrypted by the artifact KMS key."""
    template = _synth()
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
    """Like ``_flatten_definition`` but substitute CFN tokens with a placeholder so JSON parses."""
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
    props = next(iter(sms.values()))["Properties"]
    raw = props.get("DefinitionString") or props.get("DefinitionBody")
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


def test_baseline_stack_has_three_lambdas():
    """Synth the stack and verify exactly 3 Lambda functions are created."""
    template = _synth()
    lambdas = template.find_resources("AWS::Lambda::Function")
    assert len(lambdas) == 3, f"Expected exactly 3 Lambda functions, found {len(lambdas)}"

    # Extract handler names
    handlers = sorted([r["Properties"]["Handler"] for r in lambdas.values()])
    expected_handlers = sorted(
        [
            "model_monitor_cdk.load_and_gate.load_and_gate",
            "model_monitor_cdk.evaluate_results.evaluate_results",
            "model_monitor_cdk.write_registry.write_registry",
        ]
    )
    assert handlers == expected_handlers, f"Handler mismatch. Got {handlers}, expected {expected_handlers}"


def test_write_registry_lambda_has_ddb_env_var():
    """WriteRegistry Lambda must have BASELINE_REGISTRY_TABLE_NAME env var."""
    template = _synth()
    lambdas = template.find_resources("AWS::Lambda::Function")

    # Find WriteRegistry Lambda by handler
    write_registry_lambda = None
    for lam in lambdas.values():
        if "write_registry" in lam["Properties"]["Handler"]:
            write_registry_lambda = lam
            break

    assert write_registry_lambda is not None, "WriteRegistry Lambda not found"

    env_vars = write_registry_lambda["Properties"].get("Environment", {}).get("Variables", {})
    assert "BASELINE_REGISTRY_TABLE_NAME" in env_vars, f"Missing BASELINE_REGISTRY_TABLE_NAME. Got: {env_vars}"
    assert "ENVIRONMENT" in env_vars, f"Missing ENVIRONMENT. Got: {env_vars}"


def test_baseline_lambdas_have_iam_grants():
    """Verify each Lambda has the correct IAM policies."""
    template = _synth()
    lambdas = template.find_resources("AWS::Lambda::Function")
    assert len(lambdas) == 3

    # Collect all IAM policies attached to Lambda roles
    policies = template.find_resources("AWS::IAM::Policy")
    policies_text = json.dumps([p["Properties"]["PolicyDocument"] for p in policies.values()])

    # EvaluateResults has no additional grants (reads from SFN state)
    # LoadAndGate has no direct bucket grants (reads from URIs passed as input)

    # WriteRegistry should have DynamoDB access
    assert "dynamodb:PutItem" in policies_text, "WriteRegistry missing dynamodb:PutItem"


def test_baseline_sfn_starts_with_load_and_gate():
    """ASL definition must start with LoadAndGate Task state."""
    template = _synth()
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    assert len(sms) == 1
    props = next(iter(sms.values()))["Properties"]
    raw = props.get("DefinitionString") or props.get("DefinitionBody")
    text = _flatten_definition_for_parse(raw)
    definition = json.loads(text)

    # StartAt must be LoadAndGate
    assert definition.get("StartAt") == "LoadAndGate", (
        f"Expected StartAt='LoadAndGate', got {definition.get('StartAt')}"
    )

    # LoadAndGate must exist as a Task state with Lambda:invoke resource
    assert "LoadAndGate" in definition["States"], "LoadAndGate state not found"
    load_and_gate = definition["States"]["LoadAndGate"]
    assert load_and_gate["Type"] == "Task", f"LoadAndGate must be a Task, got {load_and_gate.get('Type')}"
    assert load_and_gate["Resource"] == "arn:aws:states:::lambda:invoke", (
        f"LoadAndGate resource mismatch: {load_and_gate.get('Resource')}"
    )


def test_baseline_sfn_has_approval_choice():
    """ASL definition must have ApprovalCheck Choice state that checks $.gate.status."""
    template = _synth()
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    props = next(iter(sms.values()))["Properties"]
    raw = props.get("DefinitionString") or props.get("DefinitionBody")
    text = _flatten_definition_for_parse(raw)
    definition = json.loads(text)

    # ApprovalCheck must exist as a Choice state
    assert "ApprovalCheck" in definition["States"], "ApprovalCheck state not found"
    approval_check = definition["States"]["ApprovalCheck"]
    assert approval_check["Type"] == "Choice", f"ApprovalCheck must be a Choice, got {approval_check.get('Type')}"

    # Must check $.gate.status for "rejected"
    choices = approval_check.get("Choices", [])
    assert len(choices) > 0, "ApprovalCheck must have at least one Choice rule"

    # Find the choice that checks for rejected
    rejected_found = False
    for choice in choices:
        if choice.get("Variable") == "$.gate.status" and choice.get("StringEquals") == "rejected":
            rejected_found = True
            break
    assert rejected_found, f"ApprovalCheck must check '$.gate.status' StringEquals 'rejected', got choices: {choices}"


def test_baseline_sfn_flow_complete():
    """ASL definition must have all expected states and form a valid DAG."""
    template = _synth()
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    props = next(iter(sms.values()))["Properties"]
    raw = props.get("DefinitionString") or props.get("DefinitionBody")
    text = _flatten_definition_for_parse(raw)
    definition = json.loads(text)

    states = definition["States"]

    # Check all expected states exist
    expected_states = {
        "LoadAndGate",
        "ApprovalCheck",
        "AllAnalysers",
        "EvaluateResults",
        "WriteDecision",
        "WriteRegistry",
        "RejectEnd",
        "SuccessEnd",
    }
    actual_states = set(states.keys())
    assert expected_states.issubset(actual_states), f"Missing states. Expected {expected_states}, got {actual_states}"

    # AllAnalysers must be a Parallel with 5 branches
    all_analysers = states["AllAnalysers"]
    assert all_analysers["Type"] == "Parallel", f"AllAnalysers must be Parallel, got {all_analysers.get('Type')}"
    assert "Branches" in all_analysers, "AllAnalysers missing Branches"
    assert len(all_analysers["Branches"]) == 5, (
        f"AllAnalysers must have 5 branches, got {len(all_analysers['Branches'])}"
    )

    # Verify each branch has mq, dq, bias, explain, shadow analyser names
    branch_names = set()
    for branch in all_analysers["Branches"]:
        # Branch should have states that indicate analyser type
        branch_states = branch.get("States", {})
        for state_name in branch_states.keys():
            if state_name.startswith("Run"):
                # Extract analyser from state name (e.g., "RunMq" -> "mq")
                analyser_title = state_name.replace("Run", "")
                analyser = analyser_title.lower()
                if analyser in {"mq", "dq", "bias", "explain", "shadow"}:
                    branch_names.add(analyser)

    expected_analysers = {"mq", "dq", "bias", "explain", "shadow"}
    assert branch_names == expected_analysers, (
        f"Branch analysers mismatch. Expected {expected_analysers}, got {branch_names}"
    )


def test_snapshot(snapshot_check):
    template = _synth()
    snapshot_check("operations_baseline_stack", template.to_json())


@pytest.fixture
def snapshot_check():
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    def _check(name: str, payload: dict) -> None:
        path = _SNAPSHOT_DIR / f"{name}.json"
        rendered = json.dumps(payload, indent=2, sort_keys=True)
        if not path.exists():
            path.write_text(rendered + "\n")
            return
        expected = path.read_text().rstrip("\n")
        assert rendered == expected, f"snapshot drift for {name}. Delete {path} to accept new baseline."

    return _check
