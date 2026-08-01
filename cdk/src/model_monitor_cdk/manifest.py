"""Manifest schema — training pipeline commitment contract."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Provenance(BaseModel):
    """Training pipeline provenance metadata.

    Attributes:
        git_sha: Git commit SHA of the training code.
        pipeline_run_id: Unique ID for the training pipeline run.
    """

    model_config = ConfigDict(extra="forbid")

    git_sha: str = Field(min_length=1)
    pipeline_run_id: str = Field(min_length=1)


class Manifest(BaseModel):
    """Training artifact manifest — signals baseline readiness.

    The training pipeline writes all artifacts under s3://baselines/<project>/v<N>/input/,
    then writes manifest.json **last**. EventBridge matches on this key suffix, triggering
    the Baseline SFN.

    Attributes:
        schema_version: Contract version (currently "1").
        project: Project name (must match config.projects[].name).
        model_version: Model version identifier (e.g., "7", "v1.2.3", semantic or custom).
        produced_at: ISO 8601 datetime when artifacts were produced.
        provenance: Git SHA + pipeline run ID for audit trail.
        artifacts: Dict of artifact_name → S3 key under input/. Producer determines keys.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    project: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    produced_at: datetime
    provenance: Provenance
    artifacts: dict[str, str] = Field(min_length=1)
