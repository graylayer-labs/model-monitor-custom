"""RED tests for BaselineConfig schema."""

from __future__ import annotations

import pytest
from model_baseline.config import BaselineConfig
from pydantic import ValidationError


def _bias_kwargs() -> dict:
    return {
        "package_group_name": "fraud-model",
        "baseline_version": 1,
        "monitor_type": "BIAS",
        "input_s3_uri": "s3://bucket/input.parquet",
        "config_s3_uri": "s3://bucket/config.json",
        "output_s3_uri": "s3://bucket/out/",
    }


def test_minimum_bias_config_parses():
    cfg = BaselineConfig(**_bias_kwargs())
    assert cfg.monitor_type == "BIAS"
    assert cfg.model_s3_uri is None


def test_explainability_requires_model_uri():
    kwargs = _bias_kwargs()
    kwargs["monitor_type"] = "EXPLAINABILITY"
    with pytest.raises(ValidationError):
        BaselineConfig(**kwargs)


def test_explainability_with_model_uri_parses():
    kwargs = _bias_kwargs()
    kwargs["monitor_type"] = "EXPLAINABILITY"
    kwargs["model_s3_uri"] = "s3://bucket/model.tar.gz"
    cfg = BaselineConfig(**kwargs)
    assert cfg.model_s3_uri == "s3://bucket/model.tar.gz"


def test_extra_field_rejected():
    kwargs = _bias_kwargs()
    kwargs["surprise"] = "boom"
    with pytest.raises(ValidationError):
        BaselineConfig(**kwargs)


def test_invalid_s3_uri_rejected():
    kwargs = _bias_kwargs()
    kwargs["input_s3_uri"] = "http://bucket/input"
    with pytest.raises(ValidationError):
        BaselineConfig(**kwargs)


def test_negative_baseline_version_rejected():
    kwargs = _bias_kwargs()
    kwargs["baseline_version"] = -1
    with pytest.raises(ValidationError):
        BaselineConfig(**kwargs)
