"""End-to-end synth test for the CDK app under a single-account topology."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import aws_cdk as cdk
import yaml
from aws_cdk.assertions import Template

_APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_APP_DIR))

from app import build_app  # noqa: E402


def _fixture_configs(tmp_path: Path) -> tuple[Path, Path]:
    accounts = {
        "region": "eu-west-1",
        "roles": {
            "artifact": "111111111111",
            "operations": "111111111111",
            "inference": ["111111111111"],
        },
    }
    projects = {
        "projects": [
            {
                "name": "example-classifier",
                "inference_account": "111111111111",
                "producer_bucket_arn": "arn:aws:s3:::example-training-snapshots",
            },
        ],
    }
    a = tmp_path / "accounts.yaml"
    p = tmp_path / "projects.yaml"
    a.write_text(yaml.safe_dump(accounts))
    p.write_text(yaml.safe_dump(projects))
    return a, p


def test_single_account_collapse_materialises_four_stacks(tmp_path: Path) -> None:
    accounts_path, projects_path = _fixture_configs(tmp_path)
    app = cdk.App(
        context={
            "accounts": str(accounts_path),
            "projects": str(projects_path),
        },
    )
    build_app(app)

    stacks = [child for child in app.node.children if isinstance(child, cdk.Stack)]
    assert len(stacks) == 4
    stack_ids = {s.node.id for s in stacks}
    assert stack_ids == {
        "MMC-Test-Artifact",
        "MMC-Test-SharedIam",
        "MMC-Test-InferenceMonitor-example-classifier",
        "MMC-Test-OperationsBaseline-example-classifier",
    }
    for stack in stacks:
        assert stack.account == "111111111111"
        assert stack.region == "eu-west-1"


def test_multi_project_multi_account(tmp_path: Path) -> None:
    accounts = {
        "region": "eu-west-1",
        "roles": {
            "artifact": "111111111111",
            "operations": "222222222222",
            "inference": ["333333333333", "444444444444"],
        },
    }
    projects = {
        "projects": [
            {
                "name": "p1",
                "inference_account": "333333333333",
                "producer_bucket_arn": "arn:aws:s3:::p1-training",
            },
            {
                "name": "p2",
                "inference_account": "444444444444",
                "producer_bucket_arn": "arn:aws:s3:::p2-training",
            },
        ],
    }
    a = tmp_path / "a.yaml"
    p = tmp_path / "p.yaml"
    a.write_text(yaml.safe_dump(accounts))
    p.write_text(yaml.safe_dump(projects))
    app = cdk.App(context={"accounts": str(a), "projects": str(p)})
    build_app(app)
    stacks = [c for c in app.node.children if isinstance(c, cdk.Stack)]
    # 1 artifact + 1 shared-iam + 2 * (inference + operations) = 6
    assert len(stacks) == 6
    accounts_per_stack = {s.node.id: s.account for s in stacks}
    assert accounts_per_stack["MMC-Test-Artifact"] == "111111111111"
    assert accounts_per_stack["MMC-Test-SharedIam"] == "111111111111"
    assert accounts_per_stack["MMC-Test-InferenceMonitor-p1"] == "333333333333"
    assert accounts_per_stack["MMC-Test-InferenceMonitor-p2"] == "444444444444"
    assert accounts_per_stack["MMC-Test-OperationsBaseline-p1"] == "222222222222"


def test_producer_bucket_arn_flows_from_config(tmp_path: Path) -> None:
    """Per-project producer_bucket_arn must land inside the OperationsBaselineStack synth."""
    accounts_path, projects_path = _fixture_configs(tmp_path)
    app = cdk.App(
        context={
            "accounts": str(accounts_path),
            "projects": str(projects_path),
        },
    )
    build_app(app)
    ops = next(
        c
        for c in app.node.children
        if isinstance(c, cdk.Stack) and c.node.id == "MMC-Test-OperationsBaseline-example-classifier"
    )
    rendered = json.dumps(Template.from_stack(ops).to_json())
    assert "example-training-snapshots" in rendered


def test_writer_role_arn_flows_from_shared_iam_name(tmp_path: Path) -> None:
    """Ops stack must reference the deterministic writer role ARN from artifact account."""
    accounts_path, projects_path = _fixture_configs(tmp_path)
    app = cdk.App(
        context={
            "accounts": str(accounts_path),
            "projects": str(projects_path),
        },
    )
    build_app(app)
    ops = next(
        c
        for c in app.node.children
        if isinstance(c, cdk.Stack) and c.node.id == "MMC-Test-OperationsBaseline-example-classifier"
    )
    rendered = json.dumps(Template.from_stack(ops).to_json())
    assert "arn:aws:iam::111111111111:role/mmc-test-baseline-writer" in rendered
    assert "placeholder" not in rendered
