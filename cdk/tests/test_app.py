"""End-to-end synth test for the CDK app under a single-account topology."""

from __future__ import annotations

import sys
from pathlib import Path

import aws_cdk as cdk
import yaml

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
            {"name": "example-classifier", "inference_account": "111111111111"},
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
            {"name": "p1", "inference_account": "333333333333"},
            {"name": "p2", "inference_account": "444444444444"},
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
