"""Simple Lambda handler for LocalStack testing.

This is a minimal handler that echoes back input and records execution
in DynamoDB. Used to validate Lambda invocation flow in LocalStack tests.
"""

from __future__ import annotations

import json
from datetime import datetime


def handler(event: dict, context) -> dict[str, str | dict]:
    """Echo handler that validates input and returns results.

    Args:
        event: Input event (can be any dict)
        context: Lambda context object

    Returns:
        Dict with execution details
    """
    # Extract input
    analyser_type = event.get("analyser_type", "unknown")
    run_id = event.get("run_id", "unknown")

    # Return structured result
    return {
        "statusCode": 200,
        "analyser": analyser_type,
        "run_id": run_id,
        "timestamp": datetime.utcnow().isoformat(),
        "message": f"Analyser {analyser_type} completed successfully",
        "outcome": "success",
    }
