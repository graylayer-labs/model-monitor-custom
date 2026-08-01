"""EvaluateResults Lambda — check required analysers succeeded in baseline."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


class EvaluateResultsInput(BaseModel):
    """Lambda input for EvaluateResults step in Baseline SFN.

    Attributes:
        gate_output: LoadAndGate output (status, analysers_to_run).
        analyser_results: Dict of analyser_type → result (analyser, outcome).
        required_analysers: List of analyser types that must succeed.
    """

    gate_output: dict = Field(min_length=1)
    analyser_results: dict = Field(min_length=1)
    required_analysers: list[str] = Field(min_length=1)


@dataclass
class EvaluateResultsOutput:
    """Lambda output for EvaluateResults step.

    Attributes:
        status: "approved" if all required analysers OK, else "rejected".
        message: Reason for the status.
        all_required_ok: Boolean flag for downstream logic.
    """

    status: str  # "approved" | "rejected"
    message: str
    all_required_ok: bool


def evaluate_results(event: dict, context: object) -> dict:
    """EvaluateResults Lambda handler — check required analysers succeeded.

    Args:
        event: SFN input dict with gate_output, analyser_results, required_analysers.
        context: Lambda context (unused).

    Returns:
        Dict with status, message, all_required_ok.

    Flow:
    1. Parse input (gate_output, analyser_results, required_analysers)
    2. Check each required analyser in results
    3. If any required analyser missing or failed → rejected
    4. If all required OK → approved
    5. Return status and boolean flag for Step Functions choice
    """
    # Parse input
    eval_input = EvaluateResultsInput(**event)

    # Check required analysers
    missing_or_failed = []
    for required in eval_input.required_analysers:
        if required not in eval_input.analyser_results:
            missing_or_failed.append(f"{required} (missing)")
        else:
            result = eval_input.analyser_results[required]
            if result.get("outcome") not in ("ok", "succeeded"):
                missing_or_failed.append(f"{required} ({result.get('outcome')})")

    if missing_or_failed:
        output = EvaluateResultsOutput(
            status="rejected",
            message=f"Required analysers failed: {', '.join(missing_or_failed)}",
            all_required_ok=False,
        )
    else:
        output = EvaluateResultsOutput(
            status="approved",
            message="All required analysers passed",
            all_required_ok=True,
        )

    return {
        "status": output.status,
        "message": output.message,
        "all_required_ok": output.all_required_ok,
    }
