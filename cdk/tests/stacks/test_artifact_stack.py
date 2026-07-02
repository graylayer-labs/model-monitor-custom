"""Synth + snapshot tests for ArtifactStack."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aws_cdk import App
from aws_cdk.assertions import Match, Template
from model_monitor_cdk.stacks.artifact_stack import ArtifactStack, ArtifactStackProps

CONSUMER_A = "111111111111"
CONSUMER_B = "222222222222"
OPERATIONS = "333333333333"
_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def _valid_props(**overrides) -> ArtifactStackProps:
    kwargs: dict = {
        "environment": "test",
        "consumer_account_ids": [CONSUMER_A, CONSUMER_B],
        "operations_account_id": OPERATIONS,
    }
    kwargs.update(overrides)
    return ArtifactStackProps(**kwargs)


def _synth(props: ArtifactStackProps | None = None) -> Template:
    app = App()
    stack = ArtifactStack(app, "MMC-Test-Artifact", props=props or _valid_props())
    return Template.from_stack(stack)


def test_synth_valid_props():
    template = _synth()
    assert template is not None


def test_ecr_repo_count_matches_default():
    template = _synth()
    template.resource_count_is("AWS::ECR::Repository", 7)


def test_ecr_repo_count_matches_custom():
    props = _valid_props(container_names=("baseline", "analyser-dq"))
    template = _synth(props)
    template.resource_count_is("AWS::ECR::Repository", 2)


def test_kms_key_rotation_enabled():
    template = _synth()
    template.has_resource_properties(
        "AWS::KMS::Key",
        {"EnableKeyRotation": True},
    )


def test_kms_alias_environment_scoped():
    template = _synth()
    template.has_resource_properties(
        "AWS::KMS::Alias",
        {"AliasName": "alias/mmc-test-artifacts"},
    )


def test_bucket_block_public_access_and_kms_encryption():
    template = _synth()
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {
                        "ServerSideEncryptionByDefault": Match.object_like(
                            {"SSEAlgorithm": "aws:kms"},
                        ),
                    },
                ],
            },
        },
    )


def test_bucket_policy_grants_getobject_to_each_consumer():
    template = _synth()
    policies = template.find_resources("AWS::S3::BucketPolicy")
    assert policies, "expected an AWS::S3::BucketPolicy resource"
    policy_doc = next(iter(policies.values()))["Properties"]["PolicyDocument"]
    rendered = json.dumps(policy_doc)
    for account in (CONSUMER_A, CONSUMER_B):
        assert account in rendered, f"consumer {account} not in bucket policy"
    assert OPERATIONS in rendered
    assert "s3:GetObject" in rendered
    assert "s3:PutObject" in rendered


def test_outputs_present():
    template = _synth()
    outputs = template.find_outputs("*")
    assert "BaselinesBucketName" in outputs
    assert "BaselinesBucketArn" in outputs
    assert "KmsKeyArn" in outputs


def test_ecr_repo_uri_outputs_present():
    template = _synth()
    outputs = template.find_outputs("*")
    for name in (
        "analyser-base",
        "analyser-bias",
        "analyser-dq",
        "analyser-mq",
        "analyser-explain",
        "analyser-shadow",
        "baseline",
    ):
        key = f"RepoUri{name.replace('-', '')}"
        matches = [k for k in outputs if k.startswith("RepoUri")]
        assert any(name.replace("-", "") in k for k in matches), (
            f"missing output for {name} (checked {key} against {matches})"
        )


def test_non_12_digit_consumer_rejected():
    with pytest.raises(ValueError, match="12 digits"):
        _valid_props(consumer_account_ids=["1234"])


def test_empty_consumer_list_rejected():
    with pytest.raises(ValueError, match="at least one"):
        _valid_props(consumer_account_ids=[])


def test_non_12_digit_operations_rejected():
    with pytest.raises(ValueError, match="operations_account_id"):
        _valid_props(operations_account_id="abc")


def test_invalid_environment_rejected():
    with pytest.raises(ValueError, match="environment"):
        _valid_props(environment="staging")


def test_no_ambient_account_in_bucket_or_ecr_policies():
    """Bucket + ECR policies must reference props-provided accounts only.

    KMS key default admin policy legitimately Ref's AWS::AccountId for the
    deploying account's root — that's CDK boilerplate and is exempt.
    """
    template = _synth()
    for policy in template.find_resources("AWS::S3::BucketPolicy").values():
        rendered = json.dumps(policy["Properties"]["PolicyDocument"])
        assert '"Ref": "AWS::AccountId"' not in rendered
    for policy in template.find_resources("AWS::ECR::Repository").values():
        rendered = json.dumps(policy["Properties"].get("RepositoryPolicyText", {}))
        assert '"Ref": "AWS::AccountId"' not in rendered
    full = json.dumps(template.to_json())
    for token in (CONSUMER_A, CONSUMER_B, OPERATIONS):
        assert token in full


def test_snapshot(snapshot_check):
    template = _synth()
    snapshot_check("artifact_stack", template.to_json())


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
