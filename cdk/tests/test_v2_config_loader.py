"""Tests for v2 config loader — YAML → JSON for S3."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from model_monitor_cdk.config_loader import ConfigLoader, ProjectConfigVersion


def test_config_loader_loads_project_yaml(tmp_path: Path):
    """ConfigLoader should read project YAML and serialize to JSON."""
    project_yaml = tmp_path / "projects.yaml"
    project_yaml.write_text(
        """
projects:
  - name: test-project
    inference_account: "123456789012"
    producer_bucket_arn: "arn:aws:s3:::test-bucket"
    monitors:
      model_quality:
        enabled: true
        required: true
        ground_truth:
          lookback: "7d"
          min_coverage: 0.30
      data_quality:
        enabled: true
        required: false
    endpoints:
      - name: test-endpoint
        schedule: "cron(0 * * * ? *)"
        shadow_variant: null
"""
    )
    loader = ConfigLoader(project_yaml)
    json_str = loader.to_json()

    data = json.loads(json_str)
    assert data["projects"][0]["name"] == "test-project"
    assert data["projects"][0]["monitors"]["model_quality"]["enabled"] is True


def test_config_loader_validates_config(tmp_path: Path):
    """ConfigLoader should validate config against schema."""
    project_yaml = tmp_path / "projects.yaml"
    project_yaml.write_text(
        """
projects:
  - name: test-project
    inference_account: invalid-account
    producer_bucket_arn: "arn:aws:s3:::test-bucket"
"""
    )
    with pytest.raises(ValueError, match="account"):
        ConfigLoader(project_yaml)


def test_config_loader_generates_s3_key():
    """ConfigLoader should generate versioned S3 object key."""
    loader = ConfigLoader.__new__(ConfigLoader)
    loader.projects = None

    key = loader.s3_key_for_project("test-project", version=1)
    assert key == "test-project/v1/config.json"

    key = loader.s3_key_for_project("my-model", version=42)
    assert key == "my-model/v42/config.json"


def test_config_loader_project_config_version():
    """ProjectConfigVersion should track project name and version."""
    version = ProjectConfigVersion(project_name="test-project", version=1)
    assert version.project_name == "test-project"
    assert version.version == 1
    assert version.s3_key == "test-project/v1/config.json"


def test_config_loader_missing_file_raises(tmp_path: Path):
    """ConfigLoader should raise if YAML file not found."""
    missing = tmp_path / "nonexistent.yaml"
    with pytest.raises(FileNotFoundError):
        ConfigLoader(missing)


def test_config_loader_filters_project_by_name(tmp_path: Path):
    """ConfigLoader can extract config for one project."""
    project_yaml = tmp_path / "projects.yaml"
    project_yaml.write_text(
        """
projects:
  - name: project-a
    inference_account: "111111111111"
    producer_bucket_arn: "arn:aws:s3:::bucket-a"
  - name: project-b
    inference_account: "222222222222"
    producer_bucket_arn: "arn:aws:s3:::bucket-b"
"""
    )
    loader = ConfigLoader(project_yaml)
    project_json = loader.to_json_for_project("project-a")

    data = json.loads(project_json)
    assert data["name"] == "project-a"
    assert data["inference_account"] == "111111111111"
