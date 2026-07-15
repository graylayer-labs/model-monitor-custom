"""Synth tests for GithubOidcStack."""

from __future__ import annotations

import json
from typing import Any

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template
from model_monitor_cdk.stacks.github_oidc_stack import (
    GithubOidcStack,
    GithubOidcStackProps,
)

_ARTIFACT_ACCOUNT = "111111111111"
_REGION = "eu-west-1"
_REPO = "OWNER/model-monitor-custom"


def _valid_props(**overrides: Any) -> GithubOidcStackProps:
    kwargs: dict[str, Any] = {
        "environment": "test",
        "github_repo": _REPO,
    }
    kwargs.update(overrides)
    return GithubOidcStackProps(**kwargs)


def _synth(props: GithubOidcStackProps | None = None) -> Template:
    app = App()
    stack = GithubOidcStack(
        app,
        "MMC-Test-GithubOidc",
        props=props or _valid_props(),
        env=Environment(account=_ARTIFACT_ACCOUNT, region=_REGION),
    )
    return Template.from_stack(stack)


def test_synth_ok():
    _synth()


def test_creates_oidc_provider_by_default():
    template = _synth()
    providers = template.find_resources("Custom::AWSCDKOpenIdConnectProvider")
    assert len(providers) == 1


def test_skips_oidc_provider_when_disabled():
    template = _synth(_valid_props(create_oidc_provider=False))
    assert template.find_resources("Custom::AWSCDKOpenIdConnectProvider") == {}


def test_role_trust_scoped_to_repo_and_ref():
    template = _synth()
    rendered = json.dumps(
        [r["Properties"].get("AssumeRolePolicyDocument") for r in template.find_resources("AWS::IAM::Role").values()],
    )
    assert "sts:AssumeRoleWithWebIdentity" in rendered
    assert "token.actions.githubusercontent.com:aud" in rendered
    assert "token.actions.githubusercontent.com:sub" in rendered
    assert f"repo:{_REPO}:ref:refs/heads/main" in rendered


def test_role_trust_honours_custom_ref_filter():
    template = _synth(_valid_props(ref_filter="refs/tags/*"))
    rendered = json.dumps(
        [r["Properties"].get("AssumeRolePolicyDocument") for r in template.find_resources("AWS::IAM::Role").values()],
    )
    assert f"repo:{_REPO}:ref:refs/tags/*" in rendered


def test_ecr_push_scoped_to_analyser_repos():
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    rendered = json.dumps([p["Properties"]["PolicyDocument"] for p in policies.values()])
    for repo in [
        "mmc/analyser-base",
        "mmc/analyser-bias",
        "mmc/analyser-dq",
        "mmc/analyser-mq",
        "mmc/analyser-explain",
        "mmc/analyser-shadow",
    ]:
        assert f":repository/{repo}" in rendered
    assert "ecr:PutImage" in rendered
    assert "ecr:GetAuthorizationToken" in rendered


def test_role_name_uses_environment():
    template = _synth(_valid_props(environment="prod"))
    template.has_resource_properties(
        "AWS::IAM::Role",
        {"RoleName": "mmc-prod-github-ecr-push"},
    )


def test_output_present():
    template = _synth()
    template.has_output("PushRoleArn", Match.any_value())


def test_bad_repo_rejected():
    with pytest.raises(ValueError, match="github_repo"):
        _valid_props(github_repo="not-a-slug")


def test_empty_ref_rejected():
    with pytest.raises(ValueError, match="ref_filter"):
        _valid_props(ref_filter="")


def test_empty_ecr_repos_rejected():
    with pytest.raises(ValueError, match="ecr_repo_names"):
        _valid_props(ecr_repo_names=())
