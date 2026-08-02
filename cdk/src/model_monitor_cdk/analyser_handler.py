"""Zip-based Lambda handler for analyser execution.

This handler runs analysers as Lambda functions without container images.
It's used for testing and can be extended for production Lambda deployments.

The handler imports the analyser class directly and calls _run_analyser().
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from mmc_base.contract import EnvContract
from mmc_base.entrypoint import _emit_failure, _emit_success, _run_analyser

logger = logging.getLogger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for analyser execution.

    Args:
        event: Step Functions payload containing:
            - PROJECT_NAME: Project identifier
            - RUN_ID: Execution run ID
            - ANALYSER_TYPE: Type of analyser (mq, dq, bias, explain, shadow)
            - INPUT_URIS_JSON: JSON string of input URIs
            - OUTPUT_URI: S3 prefix for outputs
            - CONFIG_URI: S3 URI of config
            - VARIANT: Analysis variant (baseline or monitor)
        context: Lambda context

    Returns:
        dict with analyser, outcome, and optional error details
    """
    try:
        # Merge event payload with environment variables
        # Event keys take precedence for per-invocation overrides
        merged_env = {**os.environ, **event}

        # Parse environment contract
        env = EnvContract.from_env(merged_env)

        # Run analyser
        logger.info(f"Running {env.ANALYSER_TYPE} analyser for {env.PROJECT_NAME}")
        output = _run_analyser(env)

        # Emit success
        _emit_success(env, output)

        return {
            "analyser": env.ANALYSER_TYPE,
            "outcome": output.outcome.value,
            "message": "Analysis complete",
        }

    except Exception as exc:
        logger.exception(f"Analyser handler failed: {exc}")

        # Try to emit failure (may fail if env not fully parsed)
        try:
            env = EnvContract.from_env(event)
            _emit_failure(env, exc)
        except Exception as parse_exc:
            logger.exception(f"Failed to emit failure: {parse_exc}")

        return {
            "analyser": event.get("ANALYSER_TYPE", "unknown"),
            "outcome": "failed",
            "error": str(exc),
        }
