"""Tests for manifest schema — training pipeline commitment contract."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from model_monitor_cdk.manifest import Manifest


def test_manifest_accepts_valid_shape():
    """Manifest should accept valid training artifact declaration."""
    manifest_dict = {
        "schema_version": "1",
        "project": "test-project",
        "model_version": "7",
        "produced_at": "2026-07-31T10:00:00Z",
        "provenance": {
            "git_sha": "abc123def456",
            "pipeline_run_id": str(uuid4()),
        },
        "artifacts": {
            "training_snapshot": "input/snapshot.jsonl",
            "evaluation_results": "input/evaluation.json",
            "predictions": "input/predictions.jsonl",
            "model": "input/model.tar.gz",
        },
    }
    manifest = Manifest(**manifest_dict)
    assert manifest.project == "test-project"
    assert manifest.model_version == "7"
    assert manifest.artifacts["model"] == "input/model.tar.gz"


def test_manifest_rejects_invalid_schema_version():
    """Manifest should reject unknown schema versions."""
    manifest_dict = {
        "schema_version": "99",  # unsupported
        "project": "test-project",
        "model_version": "7",
        "produced_at": "2026-07-31T10:00:00Z",
        "provenance": {"git_sha": "abc123", "pipeline_run_id": str(uuid4())},
        "artifacts": {"model": "input/model.tar.gz"},
    }
    with pytest.raises(ValidationError):
        Manifest(**manifest_dict)


def test_manifest_requires_all_fields():
    """Manifest should require schema_version, project, model_version, produced_at, provenance, artifacts."""
    with pytest.raises(ValidationError):
        Manifest(
            schema_version="1",
            project="test-project",
            model_version="7",
            # missing produced_at, provenance, artifacts
        )


def test_manifest_produced_at_accepts_iso8601():
    """Manifest produced_at should accept ISO 8601 datetime strings."""
    manifest = Manifest(
        schema_version="1",
        project="test-project",
        model_version="7",
        produced_at="2026-07-31T10:00:00Z",
        provenance={"git_sha": "abc123", "pipeline_run_id": str(uuid4())},
        artifacts={"model": "input/model.tar.gz"},
    )
    assert isinstance(manifest.produced_at, datetime)


def test_manifest_provenance_requires_git_sha_and_pipeline_run_id():
    """Provenance should require git_sha and pipeline_run_id."""
    with pytest.raises(ValidationError):
        Manifest(
            schema_version="1",
            project="test-project",
            model_version="7",
            produced_at="2026-07-31T10:00:00Z",
            provenance={"git_sha": "abc123"},  # missing pipeline_run_id
            artifacts={"model": "input/model.tar.gz"},
        )


def test_manifest_artifacts_is_flexible_dict():
    """Artifacts should accept arbitrary string keys (producer determines what's needed)."""
    manifest = Manifest(
        schema_version="1",
        project="test-project",
        model_version="7",
        produced_at="2026-07-31T10:00:00Z",
        provenance={"git_sha": "abc123", "pipeline_run_id": str(uuid4())},
        artifacts={
            "model": "input/model.tar.gz",
            "training_snapshot": "input/train.jsonl",
            "custom_feature": "input/features.pkl",
        },
    )
    assert "custom_feature" in manifest.artifacts


def test_manifest_serializes_to_json():
    """Manifest should serialize to JSON for S3 storage."""
    manifest = Manifest(
        schema_version="1",
        project="test-project",
        model_version="7",
        produced_at="2026-07-31T10:00:00Z",
        provenance={"git_sha": "abc123", "pipeline_run_id": "run-123"},
        artifacts={"model": "input/model.tar.gz"},
    )
    json_str = manifest.model_dump_json()
    parsed = json.loads(json_str)
    assert parsed["project"] == "test-project"
    assert parsed["artifacts"]["model"] == "input/model.tar.gz"


def test_manifest_model_version_accepts_string():
    """model_version should be a string (semantic versioning or any format)."""
    manifest1 = Manifest(
        schema_version="1",
        project="test",
        model_version="1.2.3",
        produced_at="2026-07-31T10:00:00Z",
        provenance={"git_sha": "abc", "pipeline_run_id": "run-1"},
        artifacts={"model": "input/model.tar.gz"},
    )
    manifest2 = Manifest(
        schema_version="1",
        project="test",
        model_version="v42",
        produced_at="2026-07-31T10:00:00Z",
        provenance={"git_sha": "abc", "pipeline_run_id": "run-1"},
        artifacts={"model": "input/model.tar.gz"},
    )
    assert manifest1.model_version == "1.2.3"
    assert manifest2.model_version == "v42"
