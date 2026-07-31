"""Baseline registry — DynamoDB schema for baseline approval state."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalyserStatus(str, Enum):
    """Outcome of a single analyser run within a baseline."""

    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"


class BaselineRegistry(BaseModel):
    """Baseline approval record — stored in DynamoDB.

    One row per baseline version. PK=project, SK=v<N>. Immutable after creation.
    Used by Activation Lambda to look up: "endpoint serves model vN—may I monitor?"

    Attributes:
        project: Project name (PK).
        model_version: Model version (becomes SK=v<model_version>).
        status: "approved" (all required analysers OK) or "rejected" (gating failed).
        baseline_prefix: S3 prefix where output artifacts are stored.
        analysers: Dict of analyser_type → AnalyserStatus (ok/skipped/failed).
        manifest_uri: S3 location of the manifest that triggered this baseline.
        evaluated_at: ISO 8601 datetime when evaluation completed.
        sfn_execution_arn: ARN of the Baseline SFN execution (audit trail).
    """

    model_config = ConfigDict(extra="forbid")

    project: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    status: Literal["approved", "rejected"]
    baseline_prefix: str = Field(min_length=1)
    analysers: dict[str, AnalyserStatus]
    manifest_uri: str = Field(min_length=1)
    evaluated_at: datetime
    sfn_execution_arn: str = Field(min_length=1)

    @property
    def ddb_pk(self) -> str:
        """DynamoDB partition key."""
        return self.project

    @property
    def ddb_sk(self) -> str:
        """DynamoDB sort key."""
        return f"v{self.model_version}"
