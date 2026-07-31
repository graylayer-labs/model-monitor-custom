"""Lambda handler for analyser execution.

Invoked by AWS Lambda with (event, context), this handler:
1. Merges static function env vars (os.environ) with per-invocation event payload
2. Constructs EnvContract from merged mapping
3. Calls the standard _run_analyser / _emit_success / _emit_failure flow
4. Returns a dict with analyser type and outcome for Step Functions
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from aws_lambda_powertools import Logger

from mmc_base import failure, io_cw, io_ddb, io_s3, provenance
from mmc_base.contract import AnalyserInputs, AnalyserOutput, EnvContract, Outcome, Severity
from mmc_base.entrypoint import (
    _emit_failure as entrypoint_emit_failure,
    _emit_success as entrypoint_emit_success,
    _import_analyser,
    _run_analyser as entrypoint_run_analyser,
    _validate_output,
    INPUT_DIR,
    ANALYSER_MODULE_ENV,
)

logger = Logger(service="mmc-lambda-handler")


def handler(event: dict, context: object) -> dict:  # noqa: ARG001
    """Lambda handler for analyser execution.

    Args:
        event: Lambda event dict containing analyser configuration:
            - project: Project name
            - run_id: Run UUID
            - analyser_type: Analyser type (mq/dq/bias/explain/shadow)
            - input_uris_json: JSON string mapping input names to S3 URIs
            - output_uri: S3 output prefix
            - config_uri: S3 config JSON URI
            - environment: Environment tag (test/dev/prod)
            - variant: Endpoint variant (default: AllTraffic)
        context: Lambda context (unused, but standard signature).

    Returns:
        A dict with keys:
        - analyser: The analyser type
        - outcome: The outcome (succeeded, failed_handled, etc)
    """
    # Map lowercase event keys (from Step Functions) to uppercase env var names.
    # Step Functions Payload sends lowercase; EnvContract expects UPPERCASE.
    key_mapping = {
        "project": "PROJECT_NAME",
        "run_id": "RUN_ID",
        "analyser_type": "ANALYSER_TYPE",
        "input_uris_json": "INPUT_URIS_JSON",
        "output_uri": "OUTPUT_URI",
        "config_uri": "CONFIG_URI",
        "environment": "ENVIRONMENT",
        "variant": "VARIANT",
    }

    # Merge os.environ (static, function-level) with event (per-invocation).
    # Event keys take precedence. Map lowercase event keys to uppercase env names.
    merged_env = dict(os.environ)
    for event_key, env_key in key_mapping.items():
        if event_key in event:
            merged_env[env_key] = event[event_key]

    # Parse and validate the merged contract.
    env = EnvContract.from_env(merged_env)
    logger.append_keys(
        run_id=str(env.RUN_ID),
        analyser_type=env.ANALYSER_TYPE,
        project=env.PROJECT_NAME,
        environment=env.ENVIRONMENT,
        variant=env.VARIANT,
    )

    started_at = datetime.now(UTC)
    logger.info("lambda handler start")

    try:
        output = entrypoint_run_analyser(env, INPUT_DIR)
        entrypoint_emit_success(output, env)
    except Exception as exc:  # noqa: BLE001
        entrypoint_emit_failure(exc, env, started_at)
        return {"analyser": env.ANALYSER_TYPE, "outcome": "failed_unhandled"}

    logger.info("lambda handler complete", extra={"outcome": output.outcome.value})
    return {"analyser": env.ANALYSER_TYPE, "outcome": output.outcome.value}
