"""WriteRegistry Lambda — record baseline approval in DynamoDB."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

import boto3
from pydantic import BaseModel, Field

from model_monitor_cdk.baseline_registry import BaselineRegistry


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

    Note:
        May raise KeyError if BASELINE_REGISTRY_TABLE_NAME env var is not set,
        or ClientError if DynamoDB put_item operation fails.
    """
    # Parse input
    registry_input = WriteRegistryInput(**event)

    # Get table name from environment
    table_name = os.environ["BASELINE_REGISTRY_TABLE_NAME"]

    # Construct BaselineRegistry instance
    registry = BaselineRegistry(
        project=registry_input.project,
        model_version=registry_input.model_version,
        status=registry_input.status,
        baseline_prefix=registry_input.baseline_prefix,
        analysers=registry_input.analysers,
        manifest_uri=registry_input.manifest_uri,
        evaluated_at=datetime.now(UTC),
        sfn_execution_arn=registry_input.sfn_execution_arn,
    )

    # Write to DynamoDB
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    table.put_item(
        Item={
            "project": registry.project,
            "sk": registry.ddb_sk,
            "status": registry.status,
            "baseline_prefix": registry.baseline_prefix,
            "analysers": registry.analysers,
            "manifest_uri": registry.manifest_uri,
            "sfn_execution_arn": registry.sfn_execution_arn,
            "evaluated_at": registry.evaluated_at.isoformat(),
        }
    )

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
