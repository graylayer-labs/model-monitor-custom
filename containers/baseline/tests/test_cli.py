"""RED tests for the CLI env-var config resolver."""

from __future__ import annotations

import pytest
from model_baseline.cli import resolve_config_from_env
from pydantic import ValidationError


def _bias_env() -> dict[str, str]:
    return {
        "PACKAGE_GROUP_NAME": "fraud-model",
        "BASELINE_VERSION": "1",
        "MONITOR_TYPE": "BIAS",
        "INPUT_S3_URI": "s3://bucket/in.parquet",
        "CONFIG_S3_URI": "s3://bucket/spec.json",
        "OUTPUT_S3_URI": "s3://bucket/out/",
    }


def test_full_env_returns_config():
    cfg = resolve_config_from_env(_bias_env())
    assert cfg.package_group_name == "fraud-model"
    assert cfg.baseline_version == 1


def test_missing_required_env_raises_keyerror():
    env = _bias_env()
    del env["PACKAGE_GROUP_NAME"]
    with pytest.raises(KeyError, match="PACKAGE_GROUP_NAME"):
        resolve_config_from_env(env)


def test_explainability_missing_model_uri_raises_validation_error():
    env = _bias_env()
    env["MONITOR_TYPE"] = "EXPLAINABILITY"
    with pytest.raises(ValidationError):
        resolve_config_from_env(env)
