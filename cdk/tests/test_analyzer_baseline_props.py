"""RED tests for AnalyzerBaselineProps."""

from __future__ import annotations

import dataclasses

import pytest
from model_monitor_cdk.constructs.analyzer_baseline import AnalyzerBaselineProps


def _valid_kwargs() -> dict:
    return {
        "image_uri": "123456789012.dkr.ecr.eu-west-1.amazonaws.com/model-baseline:1",
        "execution_role_arn": "arn:aws:iam::123456789012:role/exec",
        "baselines_bucket_name": "my-baselines",
        "baselines_bucket_account_id": "123456789012",
    }


def test_valid_props_construct():
    props = AnalyzerBaselineProps(**_valid_kwargs())
    assert props.input_event_bus == "default"
    assert props.max_retries == 3


def test_empty_image_uri_raises():
    kwargs = _valid_kwargs()
    kwargs["image_uri"] = ""
    with pytest.raises(ValueError, match="image_uri"):
        AnalyzerBaselineProps(**kwargs)


def test_non_12_digit_account_id_rejected():
    kwargs = _valid_kwargs()
    kwargs["baselines_bucket_account_id"] = "1234"
    with pytest.raises(ValueError, match="12 digits"):
        AnalyzerBaselineProps(**kwargs)


def test_frozen():
    props = AnalyzerBaselineProps(**_valid_kwargs())
    with pytest.raises(dataclasses.FrozenInstanceError):
        props.max_retries = 5  # ty: ignore[invalid-assignment]
