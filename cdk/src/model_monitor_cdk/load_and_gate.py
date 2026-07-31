"""LoadAndGate Lambda — fetch manifest + config, gate baseline execution."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


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

    # TODO: Fetch manifest from S3 (boto3.s3.get_object)
    # TODO: Fetch config from S3 (boto3.s3.get_object)
    # TODO: Run gate logic (baseline_gate.GateLogic)
    # TODO: Build output with analysers_to_run

    # Placeholder: return approved with all analysers
    output = LoadAndGateOutput(
        status="approved",
        message="Gating passed (placeholder)",
        analysers_to_run=["mq", "dq", "bias", "explain", "shadow"],
    )

    return {
        "status": output.status,
        "message": output.message,
        "analysers_to_run": output.analysers_to_run,
    }
