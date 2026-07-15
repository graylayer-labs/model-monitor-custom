"""Tests for :mod:`model_monitor_cdk.config` — YAML loader and validators."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml
from model_monitor_cdk.config import (
    AccountsConfig,
    EnvConfig,
    ProjectsConfig,
    ProjectSpec,
    RolesConfig,
    load_env,
    resolve_env_from_context,
)
from pydantic import ValidationError


def _write_yaml(path: Path, payload: dict | str) -> Path:
    """Write ``payload`` to ``path`` as YAML.

    Args:
        path: Destination path.
        payload: A dict (dumped) or a raw string (written verbatim).

    Returns:
        The path (for chaining).
    """
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def _accounts_payload(**overrides) -> dict:
    base = {
        "region": "eu-west-1",
        "roles": {
            "artifact": "111111111111",
            "operations": "111111111111",
            "inference": ["111111111111"],
        },
    }
    base.update(overrides)
    return base


def _projects_payload(**overrides) -> dict:
    project = {
        "name": "example-classifier",
        "inference_account": "111111111111",
        "producer_bucket_arn": "arn:aws:s3:::example-training-snapshots",
    }
    project.update(overrides)
    return {"projects": [project]}


def test_single_account_config_loads(tmp_path: Path) -> None:
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", _accounts_payload())
    projects_path = _write_yaml(tmp_path / "projects.yaml", _projects_payload())
    cfg = load_env(accounts_path, projects_path)
    assert cfg.accounts.region == "eu-west-1"
    assert cfg.accounts.roles.artifact == "111111111111"
    assert cfg.accounts.roles.operations == "111111111111"
    assert cfg.accounts.roles.inference == ["111111111111"]
    assert len(cfg.projects.projects) == 1
    assert cfg.projects.projects[0].schedule == "cron(0 * * * ? *)"
    assert cfg.projects.projects[0].vpc_id is None


def test_split_artifacts_config_loads(tmp_path: Path) -> None:
    accounts = _accounts_payload(
        roles={
            "artifact": "111111111111",
            "operations": "222222222222",
            "inference": ["222222222222"],
        },
    )
    projects = _projects_payload(inference_account="222222222222")
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", accounts)
    projects_path = _write_yaml(tmp_path / "projects.yaml", projects)
    cfg = load_env(accounts_path, projects_path)
    assert cfg.accounts.roles.artifact != cfg.accounts.roles.operations


def test_three_account_config_loads(tmp_path: Path) -> None:
    accounts = _accounts_payload(
        roles={
            "artifact": "111111111111",
            "operations": "222222222222",
            "inference": ["333333333333", "444444444444"],
        },
    )
    projects_payload = {
        "projects": [
            {
                "name": "p1",
                "inference_account": "333333333333",
                "producer_bucket_arn": "arn:aws:s3:::p1-training",
                "vpc_id": "vpc-abc",
            },
            {
                "name": "p2",
                "inference_account": "444444444444",
                "producer_bucket_arn": "arn:aws:s3:::p2-training",
                "schedule": "cron(30 * * * ? *)",
            },
        ],
    }
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", accounts)
    projects_path = _write_yaml(tmp_path / "projects.yaml", projects_payload)
    cfg = load_env(accounts_path, projects_path)
    assert len(cfg.projects.projects) == 2
    assert cfg.projects.projects[0].vpc_id == "vpc-abc"
    assert cfg.projects.projects[1].schedule == "cron(30 * * * ? *)"


def test_int_account_id_rejected(tmp_path: Path) -> None:
    accounts_path = tmp_path / "accounts.yaml"
    # YAML strips leading zeros so an unquoted 12-digit int can legitimately
    # arrive at pydantic — we must reject the int type outright.
    accounts_path.write_text(
        dedent(
            """
            region: eu-west-1
            roles:
              artifact: 111111111111
              operations: "111111111111"
              inference: ["111111111111"]
            """,
        ),
        encoding="utf-8",
    )
    projects_path = _write_yaml(tmp_path / "projects.yaml", _projects_payload())
    with pytest.raises(ValueError, match=r"artifact.*must be a string"):
        load_env(accounts_path, projects_path)


def test_eleven_digit_account_id_rejected(tmp_path: Path) -> None:
    payload = _accounts_payload()
    payload["roles"]["artifact"] = "11111111111"  # 11 digits
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", payload)
    projects_path = _write_yaml(tmp_path / "projects.yaml", _projects_payload())
    with pytest.raises(ValueError, match=r"artifact.*\\d\{12\}"):
        load_env(accounts_path, projects_path)


def test_thirteen_digit_account_id_rejected(tmp_path: Path) -> None:
    payload = _accounts_payload()
    payload["roles"]["inference"] = ["1111111111112"]
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", payload)
    projects_path = _write_yaml(tmp_path / "projects.yaml", _projects_payload())
    with pytest.raises(ValueError, match="inference"):
        load_env(accounts_path, projects_path)


def test_unknown_project_inference_account_rejected(tmp_path: Path) -> None:
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", _accounts_payload())
    projects_payload = {
        "projects": [
            {
                "name": "orphan",
                "inference_account": "999999999999",
                "producer_bucket_arn": "arn:aws:s3:::orphan-training",
            },
        ],
    }
    projects_path = _write_yaml(tmp_path / "projects.yaml", projects_payload)
    with pytest.raises(ValueError, match=r"'orphan'.*not in accounts.roles.inference"):
        load_env(accounts_path, projects_path)


def test_missing_accounts_yaml(tmp_path: Path) -> None:
    projects_path = _write_yaml(tmp_path / "projects.yaml", _projects_payload())
    with pytest.raises(FileNotFoundError, match=r"accounts\.yaml"):
        load_env(tmp_path / "accounts.yaml", projects_path)


def test_missing_projects_yaml(tmp_path: Path) -> None:
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", _accounts_payload())
    with pytest.raises(FileNotFoundError, match=r"projects\.yaml"):
        load_env(accounts_path, tmp_path / "projects.yaml")


def test_malformed_yaml_gives_clean_error(tmp_path: Path) -> None:
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text(":\n:  bad", encoding="utf-8")
    projects_path = _write_yaml(tmp_path / "projects.yaml", _projects_payload())
    with pytest.raises(ValueError, match="failed to parse YAML"):
        load_env(accounts_path, projects_path)


def test_top_level_not_mapping_rejected(tmp_path: Path) -> None:
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text("- 1\n- 2\n", encoding="utf-8")
    projects_path = _write_yaml(tmp_path / "projects.yaml", _projects_payload())
    with pytest.raises(ValueError, match="top-level mapping"):
        load_env(accounts_path, projects_path)


def test_non_list_inference_rejected(tmp_path: Path) -> None:
    payload = _accounts_payload()
    payload["roles"]["inference"] = "111111111111"  # str, not list
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", payload)
    projects_path = _write_yaml(tmp_path / "projects.yaml", _projects_payload())
    with pytest.raises(ValueError, match=r"inference must be a list"):
        load_env(accounts_path, projects_path)


def test_duplicate_inference_accounts_rejected(tmp_path: Path) -> None:
    payload = _accounts_payload()
    payload["roles"]["inference"] = ["111111111111", "111111111111"]
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", payload)
    projects_path = _write_yaml(tmp_path / "projects.yaml", _projects_payload())
    with pytest.raises(ValueError, match="unique"):
        load_env(accounts_path, projects_path)


def test_empty_project_list_rejected(tmp_path: Path) -> None:
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", _accounts_payload())
    projects_path = _write_yaml(tmp_path / "projects.yaml", {"projects": []})
    with pytest.raises(ValueError, match="projects"):
        load_env(accounts_path, projects_path)


def test_extra_fields_rejected(tmp_path: Path) -> None:
    payload = _accounts_payload()
    payload["random_extra"] = "nope"
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", payload)
    projects_path = _write_yaml(tmp_path / "projects.yaml", _projects_payload())
    with pytest.raises(ValueError, match=r"random_extra|extra"):
        load_env(accounts_path, projects_path)


def test_construct_direct_from_dict() -> None:
    cfg = EnvConfig.model_validate(
        {
            "accounts": {
                "region": "eu-west-1",
                "roles": {
                    "artifact": "111111111111",
                    "operations": "111111111111",
                    "inference": ["111111111111"],
                },
            },
            "projects": {
                "projects": [
                    {
                        "name": "p",
                        "inference_account": "111111111111",
                        "producer_bucket_arn": "arn:aws:s3:::p-training",
                    },
                ],
            },
        },
    )
    assert cfg.accounts.operations_vpc_id is None
    assert isinstance(cfg.accounts, AccountsConfig)
    assert isinstance(cfg.accounts.roles, RolesConfig)
    assert isinstance(cfg.projects, ProjectsConfig)
    assert isinstance(cfg.projects.projects[0], ProjectSpec)


def test_resolve_env_from_context(tmp_path: Path) -> None:
    import aws_cdk as cdk

    accounts_path = _write_yaml(tmp_path / "accounts.yaml", _accounts_payload())
    projects_path = _write_yaml(tmp_path / "projects.yaml", _projects_payload())
    app = cdk.App(
        context={
            "accounts": str(accounts_path),
            "projects": str(projects_path),
        },
    )
    cfg = resolve_env_from_context(app)
    assert cfg.accounts.region == "eu-west-1"


def test_resolve_env_from_context_missing_ctx() -> None:
    import aws_cdk as cdk

    app = cdk.App()
    with pytest.raises(ValueError, match=r"accounts.*projects"):
        resolve_env_from_context(app)


def test_producer_bucket_arn_valid_accepted(tmp_path: Path) -> None:
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", _accounts_payload())
    projects_path = _write_yaml(
        tmp_path / "projects.yaml",
        _projects_payload(producer_bucket_arn="arn:aws:s3:::my-training-data.v2"),
    )
    cfg = load_env(accounts_path, projects_path)
    assert cfg.projects.projects[0].producer_bucket_arn == "arn:aws:s3:::my-training-data.v2"


def test_producer_bucket_arn_empty_rejected(tmp_path: Path) -> None:
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", _accounts_payload())
    projects_path = _write_yaml(
        tmp_path / "projects.yaml",
        _projects_payload(producer_bucket_arn=""),
    )
    with pytest.raises(ValueError, match="producer_bucket_arn"):
        load_env(accounts_path, projects_path)


def test_producer_bucket_arn_non_arn_rejected(tmp_path: Path) -> None:
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", _accounts_payload())
    projects_path = _write_yaml(
        tmp_path / "projects.yaml",
        _projects_payload(producer_bucket_arn="not-an-arn"),
    )
    with pytest.raises(ValueError, match="producer_bucket_arn"):
        load_env(accounts_path, projects_path)


def test_producer_bucket_arn_non_s3_rejected(tmp_path: Path) -> None:
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", _accounts_payload())
    projects_path = _write_yaml(
        tmp_path / "projects.yaml",
        _projects_payload(producer_bucket_arn="arn:aws:sqs:eu-west-1:111111111111:q"),
    )
    with pytest.raises(ValueError, match="producer_bucket_arn"):
        load_env(accounts_path, projects_path)


def test_producer_account_defaults_to_none(tmp_path: Path) -> None:
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", _accounts_payload())
    projects_path = _write_yaml(tmp_path / "projects.yaml", _projects_payload())
    cfg = load_env(accounts_path, projects_path)
    assert cfg.projects.projects[0].producer_account is None


def test_producer_account_valid_accepted(tmp_path: Path) -> None:
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", _accounts_payload())
    projects_path = _write_yaml(
        tmp_path / "projects.yaml",
        _projects_payload(producer_account="999999999999"),
    )
    cfg = load_env(accounts_path, projects_path)
    assert cfg.projects.projects[0].producer_account == "999999999999"


def test_producer_account_bad_rejected(tmp_path: Path) -> None:
    accounts_path = _write_yaml(tmp_path / "accounts.yaml", _accounts_payload())
    projects_path = _write_yaml(
        tmp_path / "projects.yaml",
        _projects_payload(producer_account="12"),
    )
    with pytest.raises(ValueError, match="producer_account"):
        load_env(accounts_path, projects_path)


def test_project_direct_validation_error() -> None:
    with pytest.raises(ValidationError):
        AccountsConfig.model_validate(
            {
                "region": "eu-west-1",
                "roles": {
                    "artifact": 111111111111,  # int rejected
                    "operations": "111111111111",
                    "inference": ["111111111111"],
                },
            },
        )
