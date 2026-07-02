"""DynamoDB outcomes-table writer — one row per analyser run."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import boto3
from botocore.client import BaseClient

if TYPE_CHECKING:
    from mmc_base.contract import AnalyserOutput, EnvContract

_OUTCOMES_TABLE_ENV = "OUTCOMES_TABLE_NAME"

_SEVERITY_TO_SCORE: dict[str, int] = {"info": 0, "warn": 1, "alert": 2}


def _client() -> BaseClient:
    """Return the default-session DynamoDB client.

    Returns:
        The boto3 DynamoDB client.
    """
    return boto3.client("dynamodb")


def _table_name() -> str:
    """Return the outcomes-table name from env.

    Returns:
        Value of ``OUTCOMES_TABLE_NAME``.

    Raises:
        RuntimeError: If the env var is unset.
    """
    name = os.environ.get(_OUTCOMES_TABLE_ENV)
    if not name:
        msg = f"{_OUTCOMES_TABLE_ENV} not set"
        raise RuntimeError(msg)
    return name


def _duration_seconds(output: AnalyserOutput) -> float:
    """Return run duration in seconds (0.0 if end not set).

    Args:
        output: Analyser output.

    Returns:
        Wall-clock duration in seconds.
    """
    if output.run_ended_at is None:
        return 0.0
    return (output.run_ended_at - output.run_started_at).total_seconds()


def _s(v: str) -> dict[str, str]:
    """Wrap a str for the DDB attribute-value shape.

    Args:
        v: String value.

    Returns:
        ``{"S": v}``.
    """
    return {"S": v}


def _n(v: float | int) -> dict[str, str]:
    """Wrap a number for the DDB attribute-value shape.

    Args:
        v: Numeric value.

    Returns:
        ``{"N": str(v)}``.
    """
    return {"N": str(v)}


def build_item(
    output: AnalyserOutput,
    env: EnvContract,
    provenance: dict[str, Any],
    failure_uri: str | None = None,
) -> dict[str, dict[str, str]]:
    """Build the DDB item dict for this run.

    Args:
        output: Analyser output.
        env: Env contract.
        provenance: Provenance dict.
        failure_uri: Present only when the outcome is a failure.

    Returns:
        DDB attribute-value dict ready for :meth:`put_item`.
    """
    base = env.OUTPUT_URI.rstrip("/")
    item: dict[str, dict[str, str]] = {
        "run_id": _s(str(env.RUN_ID)),
        "analyser_type": _s(env.ANALYSER_TYPE),
        "project": _s(env.PROJECT_NAME),
        "environment": _s(env.ENVIRONMENT),
        "variant": _s(env.VARIANT),
        "outcome": _s(output.outcome.value),
        "severity": _s(output.resolved_severity().value),
        "violation_count": _n(output.violation_count),
        "started_at": _s(output.run_started_at.isoformat()),
        "ended_at": _s(output.run_ended_at.isoformat() if output.run_ended_at else ""),
        "duration_seconds": _n(_duration_seconds(output)),
        "image_digest": _s(str(provenance.get("image_digest", "unknown"))),
        "git_sha": _s(str(provenance.get("git_sha", "unknown"))),
        "result_s3_uri": _s(f"{base}/result.json"),
    }
    if failure_uri:
        item["failure_s3_uri"] = _s(failure_uri)
    return item


def write_outcome_row(
    output: AnalyserOutput,
    env: EnvContract,
    provenance: dict[str, Any],
    failure_uri: str | None = None,
) -> None:
    """Write one outcome row to the DDB outcomes table.

    Args:
        output: Analyser output.
        env: Env contract.
        provenance: Provenance dict.
        failure_uri: Present only when the outcome is a failure.
    """
    _client().put_item(TableName=_table_name(), Item=build_item(output, env, provenance, failure_uri))


def severity_score(sev: str) -> int:
    """Return the numeric CW score for a severity name."""
    return _SEVERITY_TO_SCORE[sev]
