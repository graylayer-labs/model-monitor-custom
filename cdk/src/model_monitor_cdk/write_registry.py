"""WriteRegistry Lambda — record baseline approval in DynamoDB."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass

from pydantic import BaseModel, Field


class WriteRegistryInput(BaseModel):
    """Lambda input for WriteRegistry step in Baseline SFN.

    Attributes:
        project: Project name (DDB PK).
        model_version: Model version (DDB SK = v<model_version>).
        status: "approved" or "rejected".
        baseline_prefix: S3 prefix where baseline outputs live.
        analysers: Dict of analyser_type → status (ok/skipped/failed).
        manifest_uri: S3 location of manifest that triggered this run.
        sfn_execution_arn: ARN of the Baseline SFN execution (audit trail).
    """

    project: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    status: str  # "approved" | "rejected"
    baseline_prefix: str = Field(min_length=1)
    analysers: dict[str, str] = Field(min_length=1)
    manifest_uri: str = Field(min_length=1)
    sfn_execution_arn: str = Field(min_length=1)


@dataclass
class WriteRegistryOutput:
    """Lambda output for WriteRegistry step.

    Attributes:
        project: Project name (for downstream reference).
        model_version: Model version (for downstream reference).
        written: Boolean flag indicating DDB write success.
    """

    project: str
    model_version: str
    written: bool


def write_registry(event: dict, context: object) -> dict:
    """WriteRegistry Lambda handler — record baseline approval in DynamoDB.

    Args:
        event: SFN input dict with project, model_version, status, etc.
        context: Lambda context (unused).

    Returns:
        Dict with project, model_version, written flag.

    Flow:
    1. Parse input (baseline record)
    2. Write to DynamoDB table (PK=project, SK=v<model_version>)
    3. Set evaluated_at = now(), include all fields
    4. Return success flag for downstream SFN steps
    """
    # Parse input
    registry_input = WriteRegistryInput(**event)

    # TODO: Write to DynamoDB table
    # - Table name from env var (or config)
    # - Item: PK=project, SK=v<model_version>
    # - Include: status, baseline_prefix, analysers, manifest_uri, sfn_execution_arn, evaluated_at

    # Placeholder: return written=True
    output = WriteRegistryOutput(
        project=registry_input.project,
        model_version=registry_input.model_version,
        written=True,
    )

    return {
        "project": output.project,
        "model_version": output.model_version,
        "written": output.written,
    }
