"""Tests for baseline registry — DynamoDB schema for baseline approval state."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from model_monitor_cdk.baseline_registry import BaselineRegistry, AnalyserStatus


def test_baseline_registry_item_accepts_valid_row():
    """BaselineRegistry should accept valid baseline approval record."""
    item = BaselineRegistry(
        project="project-a",
        model_version="7",
        status="approved",
        baseline_prefix="s3://baselines/project-a/v7/output/",
        analysers={
            "model_quality": AnalyserStatus.OK,
            "data_quality": AnalyserStatus.OK,
            "bias": AnalyserStatus.SKIPPED,
            "explainability": AnalyserStatus.SKIPPED,
            "shadow": AnalyserStatus.OK,
        },
        manifest_uri="s3://baselines/project-a/v7/input/manifest.json",
        evaluated_at=datetime.now(timezone.utc),
        sfn_execution_arn="arn:aws:states:eu-west-1:123456789012:execution:baseline-sfn:abc123",
    )
    assert item.project == "project-a"
    assert item.status == "approved"


def test_baseline_registry_requires_all_fields():
    """BaselineRegistry should require all fields."""
    with pytest.raises(ValidationError):
        BaselineRegistry(
            project="project-a",
            model_version="7",
            status="approved",
            # missing baseline_prefix, analysers, manifest_uri, evaluated_at, sfn_execution_arn
        )


def test_baseline_registry_status_validation():
    """Status should be 'approved' or 'rejected'."""
    with pytest.raises(ValidationError):
        BaselineRegistry(
            project="project-a",
            model_version="7",
            status="unknown",  # invalid
            baseline_prefix="s3://baselines/project-a/v7/output/",
            analysers={"model_quality": AnalyserStatus.OK},
            manifest_uri="s3://baselines/project-a/v7/input/manifest.json",
            evaluated_at=datetime.now(timezone.utc),
            sfn_execution_arn="arn:aws:states:eu-west-1:123456789012:execution:sfn:abc",
        )


def test_baseline_registry_analyser_status_enum():
    """AnalyserStatus should support OK, SKIPPED, FAILED."""
    assert AnalyserStatus.OK.value == "ok"
    assert AnalyserStatus.SKIPPED.value == "skipped"
    assert AnalyserStatus.FAILED.value == "failed"


def test_baseline_registry_ddb_key():
    """Registry should provide DynamoDB PK/SK for querying."""
    item = BaselineRegistry(
        project="project-a",
        model_version="7",
        status="approved",
        baseline_prefix="s3://baselines/project-a/v7/output/",
        analysers={"model_quality": AnalyserStatus.OK},
        manifest_uri="s3://baselines/project-a/v7/input/manifest.json",
        evaluated_at=datetime.now(timezone.utc),
        sfn_execution_arn="arn:aws:states:eu-west-1:123456789012:execution:sfn:abc",
    )
    # PK = project, SK = v<N>
    pk = f"{item.project}"
    sk = f"v{item.model_version}"
    assert pk == "project-a"
    assert sk == "v7"


def test_baseline_registry_partial_analyser_status():
    """Registry should handle partial analyser results (not all 5 present)."""
    item = BaselineRegistry(
        project="project-a",
        model_version="7",
        status="approved",
        baseline_prefix="s3://baselines/project-a/v7/output/",
        analysers={
            "model_quality": AnalyserStatus.OK,
            "data_quality": AnalyserStatus.FAILED,
            # bias, explain, shadow may be skipped or not run
        },
        manifest_uri="s3://baselines/project-a/v7/input/manifest.json",
        evaluated_at=datetime.now(timezone.utc),
        sfn_execution_arn="arn:aws:states:eu-west-1:123456789012:execution:sfn:abc",
    )
    assert len(item.analysers) == 2
    assert item.analysers["model_quality"] == AnalyserStatus.OK


def test_baseline_registry_rejected_row():
    """Registry should accept rejected status (e.g., gate failed)."""
    item = BaselineRegistry(
        project="project-a",
        model_version="7",
        status="rejected",
        baseline_prefix="s3://baselines/project-a/v7/output/",
        analysers={},  # no analysers run on rejection
        manifest_uri="s3://baselines/project-a/v7/input/manifest.json",
        evaluated_at=datetime.now(timezone.utc),
        sfn_execution_arn="arn:aws:states:eu-west-1:123456789012:execution:sfn:abc",
    )
    assert item.status == "rejected"
    assert len(item.analysers) == 0
