"""Tests for v2 monitor config schema."""

from __future__ import annotations

from model_monitor_cdk.config import ProjectSpec


def test_v2_project_spec_accepts_monitors_block():
    """ProjectSpec should accept monitors dict with all five analyser types."""
    spec = ProjectSpec(
        name="test-project",
        inference_account="123456789012",
        producer_bucket_arn="arn:aws:s3:::test-bucket",
        monitors={
            "model_quality": {"enabled": True, "required": True},
            "data_quality": {"enabled": True, "required": False},
            "bias": {"enabled": False},
            "explainability": {"enabled": False},
            "shadow": {"enabled": True, "required": False},
        },
    )
    assert spec.monitors is not None
    assert "model_quality" in spec.monitors
    assert spec.monitors["model_quality"].enabled is True


def test_v2_model_quality_requires_ground_truth_config():
    """Model Quality monitor must have ground_truth block with lookback and min_coverage."""
    spec = ProjectSpec(
        name="test-project",
        inference_account="123456789012",
        producer_bucket_arn="arn:aws:s3:::test-bucket",
        monitors={
            "model_quality": {
                "enabled": True,
                "required": True,
                "ground_truth": {
                    "lookback": "7d",
                    "min_coverage": 0.30,
                },
                "thresholds": {
                    "f1_min": 0.85,
                },
            },
        },
    )
    mq_config = spec.monitors["model_quality"]
    assert mq_config.ground_truth.lookback == "7d"
    assert mq_config.ground_truth.min_coverage == 0.30


def test_v2_thresholds_live_in_config():
    """Thresholds (f1_min, drift_psi_max, etc.) should live in config dict per monitor."""
    spec = ProjectSpec(
        name="test-project",
        inference_account="123456789012",
        producer_bucket_arn="arn:aws:s3:::test-bucket",
        monitors={
            "data_quality": {
                "enabled": True,
                "required": False,
                "thresholds": {
                    "drift_psi_max": 0.2,
                },
            },
        },
    )
    assert spec.monitors["data_quality"].thresholds["drift_psi_max"] == 0.2


def test_v2_endpoints_block_with_schedule_and_shadow():
    """ProjectSpec should accept endpoints list with schedule and shadow_variant."""
    spec = ProjectSpec(
        name="test-project",
        inference_account="123456789012",
        producer_bucket_arn="arn:aws:s3:::test-bucket",
        endpoints=[
            {
                "name": "test-endpoint",
                "schedule": "cron(0 * * * ? *)",
                "shadow_variant": "AllTraffic:shadow",
            },
        ],
    )
    assert spec.endpoints is not None
    assert len(spec.endpoints) > 0
    assert spec.endpoints[0].name == "test-endpoint"
    assert spec.endpoints[0].shadow_variant == "AllTraffic:shadow"


def test_v2_endpoints_shadow_variant_optional():
    """shadow_variant should be optional (can be null)."""
    spec = ProjectSpec(
        name="test-project",
        inference_account="123456789012",
        producer_bucket_arn="arn:aws:s3:::test-bucket",
        endpoints=[
            {
                "name": "test-endpoint",
                "schedule": "cron(0 * * * ? *)",
                "shadow_variant": None,
            },
        ],
    )
    assert spec.endpoints[0].shadow_variant is None


def test_v2_disabled_monitor_need_not_have_thresholds():
    """If monitor is disabled, thresholds are optional."""
    spec = ProjectSpec(
        name="test-project",
        inference_account="123456789012",
        producer_bucket_arn="arn:aws:s3:::test-bucket",
        monitors={
            "bias": {"enabled": False},
        },
    )
    assert spec.monitors["bias"].enabled is False


def test_v2_monitor_required_false_is_different_from_disabled():
    """required=False means analyser runs but missing artifacts warn (don't fail).
    enabled=False means analyser doesn't run at all."""
    spec = ProjectSpec(
        name="test-project",
        inference_account="123456789012",
        producer_bucket_arn="arn:aws:s3:::test-bucket",
        monitors={
            "data_quality": {"enabled": True, "required": False},
            "bias": {"enabled": False, "required": False},
        },
    )
    # Both exist, but semantics differ
    assert spec.monitors["data_quality"].enabled is True
    assert spec.monitors["data_quality"].required is False
    assert spec.monitors["bias"].enabled is False
