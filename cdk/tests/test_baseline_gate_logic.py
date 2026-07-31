"""Tests for baseline gate logic — manifest vs config gating."""

from __future__ import annotations

import pytest

from model_monitor_cdk.baseline_gate import GateLogic, GateOutcome
from model_monitor_cdk.config import MonitorConfig, ProjectSpec
from model_monitor_cdk.manifest import Manifest, Provenance


@pytest.fixture
def sample_manifest() -> Manifest:
    """Create a sample manifest with common artifacts."""
    return Manifest(
        schema_version="1",
        project="test-project",
        model_version="7",
        produced_at="2026-07-31T10:00:00Z",
        provenance=Provenance(git_sha="abc123", pipeline_run_id="run-1"),
        artifacts={
            "training_snapshot": "input/snapshot.jsonl",
            "evaluation_results": "input/evaluation.json",
            "predictions": "input/predictions.jsonl",
            "model": "input/model.tar.gz",
        },
    )


@pytest.fixture
def sample_project() -> ProjectSpec:
    """Create a sample project with all monitors configured."""
    return ProjectSpec(
        name="test-project",
        inference_account="123456789012",
        producer_bucket_arn="arn:aws:s3:::test-bucket",
        monitors={
            "model_quality": MonitorConfig(enabled=True, required=True),
            "data_quality": MonitorConfig(enabled=True, required=False),
            "bias": MonitorConfig(enabled=False, required=False),
            "explainability": MonitorConfig(enabled=False, required=False),
            "shadow": MonitorConfig(enabled=True, required=False),
        },
    )


def test_gate_logic_accepts_all_required_artifacts(sample_manifest, sample_project):
    """Gate logic should PASS when manifest has all required artifacts."""
    gate = GateLogic(manifest=sample_manifest, project=sample_project)

    # All monitors that are required (mq, dq) have artifacts in manifest
    outcome = gate.evaluate()
    assert outcome.status in ("approved", "approved_with_warnings")


def test_gate_logic_fails_on_missing_required_artifact(sample_manifest, sample_project):
    """Gate logic should FAIL if required monitor artifact is missing."""
    # Remove training_snapshot (needed for mq)
    sample_manifest.artifacts = {
        "evaluation_results": "input/evaluation.json",
        "predictions": "input/predictions.jsonl",
        "model": "input/model.tar.gz",
    }
    gate = GateLogic(manifest=sample_manifest, project=sample_project)
    outcome = gate.evaluate()
    assert outcome.status == "rejected"
    assert "model_quality" in outcome.message.lower() or "required" in outcome.message.lower()


def test_gate_logic_warns_on_missing_optional_artifact(sample_manifest, sample_project):
    """Gate logic should WARN (not fail) if optional monitor artifact is missing."""
    # Remove evaluation_results (optional for dq)
    sample_manifest.artifacts = {
        "training_snapshot": "input/snapshot.jsonl",
        "predictions": "input/predictions.jsonl",
        "model": "input/model.tar.gz",
    }
    gate = GateLogic(manifest=sample_manifest, project=sample_project)
    outcome = gate.evaluate()
    assert outcome.status == "approved_with_warnings"
    assert any(
        "data_quality" in w.lower() or "optional" in w.lower() for w in outcome.warnings
    )


def test_gate_logic_skips_disabled_monitors(sample_manifest, sample_project):
    """Gate logic should not require artifacts for disabled monitors."""
    # Remove artifacts that are only needed for bias/explain
    sample_manifest.artifacts = {
        "training_snapshot": "input/snapshot.jsonl",
        "predictions": "input/predictions.jsonl",
        "model": "input/model.tar.gz",
    }
    gate = GateLogic(manifest=sample_manifest, project=sample_project)
    outcome = gate.evaluate()
    # Should pass or warn (depending on optional artifacts), but not fail on disabled monitors
    assert outcome.status in ("approved", "approved_with_warnings")


def test_gate_logic_analyser_plan(sample_manifest, sample_project):
    """Gate logic should return plan of which analysers run."""
    gate = GateLogic(manifest=sample_manifest, project=sample_project)
    outcome = gate.evaluate()

    # mq, dq, shadow are enabled; bias, explain are disabled
    assert "model_quality" in outcome.analysers_to_run
    assert "data_quality" in outcome.analysers_to_run
    assert "shadow" in outcome.analysers_to_run
    assert "bias" not in outcome.analysers_to_run
    assert "explainability" not in outcome.analysers_to_run


def test_gate_outcome_structure():
    """GateOutcome should have status, message, warnings, analysers_to_run."""
    outcome = GateOutcome(
        status="approved",
        message="All checks passed",
        warnings=[],
        analysers_to_run=["mq", "dq"],
    )
    assert outcome.status == "approved"
    assert len(outcome.analysers_to_run) == 2
