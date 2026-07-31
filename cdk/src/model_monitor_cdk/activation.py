"""Activation Lambda — read baseline registry and enable monitoring if approved."""

from __future__ import annotations

import os
from dataclasses import dataclass

import boto3
from pydantic import BaseModel, Field


class ActivationInput(BaseModel):
    """Lambda input for Activation step — query if baseline approval exists for this model version.

    Attributes:
        project: Project name (DDB PK).
        model_version: Model version (DDB SK = v<model_version>).
    """

    project: str = Field(min_length=1)
    model_version: str = Field(min_length=1)


@dataclass
class ActivationOutput:
    """Lambda output for Activation step.

    Attributes:
        activate: True if approved baseline found, False otherwise.
        baseline_prefix: S3 prefix of baseline artifacts (if activate=True).
        analysers: Dict of analyser_type → status (if activate=True).
        reason: Human-readable reason if activate=False.
    """

    activate: bool
    baseline_prefix: str | None
    analysers: dict[str, str] | None
    reason: str | None


def activation(event: dict, context: object) -> dict:
    """Activation Lambda — query baseline registry, allow monitoring if approved.

    Args:
        event: Input dict with project, model_version.
        context: Lambda context (unused).

    Returns:
        Dict with activate flag, baseline_prefix, analysers (if activate=True), reason (if activate=False).

    Raises:
        KeyError: If BASELINE_REGISTRY_TABLE_NAME env var not set.

    Flow:
    1. Parse ActivationInput (project, model_version)
    2. Read BASELINE_REGISTRY_TABLE_NAME from env
    3. Query DynamoDB: get_item(Key={'project': project, 'sk': f'v{model_version}'})
    4. If item exists and status=='approved': return activate=True + baseline_prefix + analysers
    5. If item missing or status=='rejected': return activate=False + reason
    """
    try:
        activation_input = ActivationInput(**event)
        table_name = os.environ["BASELINE_REGISTRY_TABLE_NAME"]
        return _query_baseline(activation_input, table_name)
    except KeyError:
        # Missing environment variable — re-raise
        raise
    except ValueError as e:
        # Validation error — return disabled
        output = ActivationOutput(
            activate=False,
            baseline_prefix=None,
            analysers=None,
            reason=f"Validation error: {e!s}",
        )
        return _format_output(output)


def _query_baseline(
    activation_input: ActivationInput,
    table_name: str,
) -> dict:
    """Query baseline registry and return activation decision.

    Args:
        activation_input: Parsed ActivationInput.
        table_name: DynamoDB table name.

    Returns:
        Formatted output dict.
    """
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    response = table.get_item(
        Key={
            "project": activation_input.project,
            "sk": f"v{activation_input.model_version}",
        }
    )

    if "Item" not in response:
        output = ActivationOutput(
            activate=False,
            baseline_prefix=None,
            analysers=None,
            reason=f"Model version {activation_input.model_version} not found "
            f"in baseline registry for project {activation_input.project}",
        )
        return _format_output(output)

    item = response["Item"]

    if item.get("status") != "approved":
        output = ActivationOutput(
            activate=False,
            baseline_prefix=None,
            analysers=None,
            reason="Baseline rejected",
        )
        return _format_output(output)

    output = ActivationOutput(
        activate=True,
        baseline_prefix=item.get("baseline_prefix"),
        analysers=item.get("analysers"),
        reason=None,
    )
    return _format_output(output)


def _format_output(output: ActivationOutput) -> dict:
    """Format ActivationOutput as dict.

    Args:
        output: ActivationOutput instance.

    Returns:
        Dict with all fields.
    """
    return {
        "activate": output.activate,
        "baseline_prefix": output.baseline_prefix,
        "analysers": output.analysers,
        "reason": output.reason,
    }
