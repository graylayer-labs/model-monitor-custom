"""LoadAndGate Lambda — fetch manifest + config, gate baseline execution."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import boto3
from pydantic import BaseModel, Field

from model_monitor_cdk.baseline_gate import GateLogic
from model_monitor_cdk.config import ProjectSpec
from model_monitor_cdk.manifest import Manifest

logger = logging.getLogger(__name__)


class LoadAndGateInput(BaseModel):
    """Lambda input for LoadAndGate step in Baseline SFN.

    Attributes:
        manifest_uri: S3 URI of manifest.json written by training pipeline.
        config_uri: S3 URI of project config.json (versioned).
        project: Project name (used for logging/audit).
    """

    manifest_uri: str = Field(min_length=1)
    config_uri: str = Field(min_length=1)
    project: str = Field(min_length=1)


@dataclass
class LoadAndGateOutput:
    """Lambda output for LoadAndGate step.

    Attributes:
        status: "approved" (all required artifacts present) or "rejected".
        message: Reason for the status.
        analysers_to_run: List of analyser types to execute (empty if rejected).
    """

    status: str  # "approved" | "rejected"
    message: str
    analysers_to_run: list[str] = None

    def __post_init__(self):
        """Initialize analysers_to_run to empty list if None."""
        if self.analysers_to_run is None:
            self.analysers_to_run = []


def _fetch_s3_json(uri: str) -> dict[str, Any]:
    """Fetch and parse JSON from S3 URI.

    Args:
        uri: S3 URI (s3://bucket/key)

    Returns:
        Parsed JSON dict
    """
    # Parse S3 URI
    if not uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {uri}")

    parts = uri[5:].split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid S3 URI format: {uri}")

    bucket, key = parts

    # Fetch from S3
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read().decode("utf-8")
    return json.loads(body)


def load_and_gate(event: dict, context: object) -> dict:
    """LoadAndGate Lambda handler — manifest + config gating.

    Args:
        event: SFN input dict with manifest_uri, config_uri, project.
        context: Lambda context (unused).

    Returns:
        Dict with status, message, analysers_to_run.

    Flow:
    1. Parse input (manifest_uri, config_uri, project)
    2. Fetch manifest from S3
    3. Fetch config from S3
    4. Run gate logic (config requirements vs manifest artifacts)
    5. Return plan: which analysers run, status, warnings
    """
    # Parse input
    gate_input = LoadAndGateInput(**event)

    try:
        # Fetch manifest and config from S3
        logger.info(f"Fetching manifest from {gate_input.manifest_uri}")
        manifest_dict = _fetch_s3_json(gate_input.manifest_uri)
        manifest = Manifest(**manifest_dict)

        logger.info(f"Fetching config from {gate_input.config_uri}")
        config_dict = _fetch_s3_json(gate_input.config_uri)
        project = ProjectSpec(**config_dict)

        # Run gate logic
        logger.info(f"Running gate logic for {gate_input.project}")
        gate = GateLogic(manifest=manifest, project=project)
        gate_outcome = gate.evaluate()

        output = LoadAndGateOutput(
            status=gate_outcome.status,
            message=gate_outcome.message,
            analysers_to_run=gate_outcome.analysers_to_run,
        )

        if gate_outcome.warnings:
            logger.warning(f"Gate warnings: {gate_outcome.warnings}")

    except Exception as exc:
        logger.exception(f"Gate logic failed: {exc}")
        output = LoadAndGateOutput(
            status="rejected",
            message=f"Gate logic error: {exc!s}",
            analysers_to_run=[],
        )

    return {
        "status": output.status,
        "message": output.message,
        "analysers_to_run": output.analysers_to_run,
    }
