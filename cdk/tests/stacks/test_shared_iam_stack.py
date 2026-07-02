"""Synth + snapshot tests for SharedIamStack."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aws_cdk import App
from aws_cdk.assertions import Template
from model_monitor_cdk.stacks.shared_iam_stack import SharedIamStack, SharedIamStackProps

READER_A = "111111111111"
READER_B = "222222222222"
READER_C = "444444444444"
WRITER = "333333333333"
BUCKET_ARN = "arn:aws:s3:::mmc-baselines-abc"
KMS_ARN = "arn:aws:kms:eu-west-1:965377249924:key/abcd-efgh"
_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def _valid_props(**overrides) -> SharedIamStackProps:
    kwargs: dict = {
        "environment": "test",
        "reader_accounts": [READER_A, READER_B],
        "writer_account_id": WRITER,
        "baselines_bucket_arn": BUCKET_ARN,
        "artifact_kms_key_arn": KMS_ARN,
    }
    kwargs.update(overrides)
    return SharedIamStackProps(**kwargs)


def _synth(props: SharedIamStackProps | None = None) -> tuple[SharedIamStack, Template]:
    app = App()
    stack = SharedIamStack(app, "MMC-Test-SharedIam", props=props or _valid_props())
    return stack, Template.from_stack(stack)


def test_synth_valid_props():
    _, template = _synth()
    assert template is not None


def test_reader_role_count_matches_reader_accounts():
    _, template = _synth()
    # 2 readers + 1 writer
    template.resource_count_is("AWS::IAM::Role", 3)


def test_reader_role_count_scales():
    props = _valid_props(reader_accounts=[READER_A, READER_B, READER_C])
    _, template = _synth(props)
    template.resource_count_is("AWS::IAM::Role", 4)


def test_reader_role_names():
    _, template = _synth()
    for account in (READER_A, READER_B):
        template.has_resource_properties(
            "AWS::IAM::Role",
            {"RoleName": f"mmc-test-baseline-reader-{account}"},
        )


def test_writer_role_name_and_trust():
    _, template = _synth()
    template.has_resource_properties(
        "AWS::IAM::Role",
        {"RoleName": "mmc-test-baseline-writer"},
    )
    roles = template.find_resources("AWS::IAM::Role")
    writer = next(v for v in roles.values() if v["Properties"].get("RoleName") == "mmc-test-baseline-writer")
    trust = json.dumps(writer["Properties"]["AssumeRolePolicyDocument"])
    assert WRITER in trust
    assert READER_A not in trust
    assert READER_B not in trust


def test_reader_trust_policies_isolated_per_account():
    _, template = _synth()
    roles = template.find_resources("AWS::IAM::Role")
    reader_a = next(
        v for v in roles.values() if v["Properties"].get("RoleName") == f"mmc-test-baseline-reader-{READER_A}"
    )
    reader_b = next(
        v for v in roles.values() if v["Properties"].get("RoleName") == f"mmc-test-baseline-reader-{READER_B}"
    )
    trust_a = json.dumps(reader_a["Properties"]["AssumeRolePolicyDocument"])
    trust_b = json.dumps(reader_b["Properties"]["AssumeRolePolicyDocument"])
    assert READER_A in trust_a
    assert READER_B not in trust_a
    assert READER_B in trust_b
    assert READER_A not in trust_b


def test_reader_policy_read_only():
    _, template = _synth()
    roles = template.find_resources("AWS::IAM::Role")
    reader = next(
        v for v in roles.values() if v["Properties"].get("RoleName") == f"mmc-test-baseline-reader-{READER_A}"
    )
    policy = json.dumps(reader["Properties"]["Policies"])
    assert "s3:GetObject" in policy
    assert "kms:Decrypt" in policy
    assert "s3:PutObject" not in policy
    assert "kms:GenerateDataKey" not in policy


def test_writer_policy_has_put_and_get():
    _, template = _synth()
    roles = template.find_resources("AWS::IAM::Role")
    writer = next(v for v in roles.values() if v["Properties"].get("RoleName") == "mmc-test-baseline-writer")
    policy = json.dumps(writer["Properties"]["Policies"])
    assert "s3:PutObject" in policy
    assert "s3:GetObject" in policy
    assert "kms:GenerateDataKey" in policy


def test_policies_reference_passed_bucket_and_kms_arns():
    _, template = _synth()
    for role in template.find_resources("AWS::IAM::Role").values():
        rendered = json.dumps(role["Properties"]["Policies"])
        assert BUCKET_ARN in rendered
        assert KMS_ARN in rendered


def test_outputs_present():
    _, template = _synth()
    outputs = template.find_outputs("*")
    assert "BaselineWriterRoleArn" in outputs
    assert f"BaselineReaderRoleArn{READER_A}" in outputs or f"BaselineReaderRoleArn-{READER_A}" in outputs
    # CDK strips hyphens from output logical IDs; assert either form
    assert any(READER_A in k for k in outputs)
    assert any(READER_B in k for k in outputs)


def test_reader_role_arns_attribute():
    stack, _ = _synth()
    assert set(stack.reader_role_arns.keys()) == {READER_A, READER_B}


def test_no_ambient_account_id_in_template():
    _, template = _synth()
    rendered = json.dumps(template.to_json())
    assert '"Ref": "AWS::AccountId"' not in rendered


def test_no_ambient_account_or_region_in_source():
    import ast

    src = Path(__file__).resolve().parents[2] / "src" / "model_monitor_cdk" / "stacks" / "shared_iam_stack.py"
    tree = ast.parse(src.read_text())
    banned = {"account", "region"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            assert node.attr not in banned, f"forbidden self.{node.attr} in shared_iam_stack.py"


def test_empty_reader_accounts_rejected():
    with pytest.raises(ValueError, match="at least one"):
        _valid_props(reader_accounts=[])


def test_non_12_digit_reader_rejected():
    with pytest.raises(ValueError, match="12 digits"):
        _valid_props(reader_accounts=["1234"])


def test_duplicate_reader_rejected():
    with pytest.raises(ValueError, match="duplicates"):
        _valid_props(reader_accounts=[READER_A, READER_A])


def test_non_12_digit_writer_rejected():
    with pytest.raises(ValueError, match="writer_account_id"):
        _valid_props(writer_account_id="abc")


def test_empty_bucket_arn_rejected():
    with pytest.raises(ValueError, match="baselines_bucket_arn"):
        _valid_props(baselines_bucket_arn="")


def test_empty_kms_arn_rejected():
    with pytest.raises(ValueError, match="artifact_kms_key_arn"):
        _valid_props(artifact_kms_key_arn="")


def test_empty_prefix_rejected():
    with pytest.raises(ValueError, match="baseline_prefix"):
        _valid_props(baseline_prefix="")


def test_invalid_environment_rejected():
    with pytest.raises(ValueError, match="environment"):
        _valid_props(environment="staging")


def test_snapshot(snapshot_check):
    _, template = _synth()
    snapshot_check("shared_iam_stack", template.to_json())


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
