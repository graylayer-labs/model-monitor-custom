"""CloudWatch metric emitter — standard + analyser-specific metrics.

Namespace ``mmc/analyser/v1`` per design 006. Standard dims: Project,
Environment, AnalyserType, Variant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import boto3
from botocore.client import BaseClient

from mmc_base.io_ddb import severity_score

if TYPE_CHECKING:
    from mmc_base.contract import AnalyserOutput, EnvContract

NAMESPACE = "mmc/analyser/v1"


def _client() -> BaseClient:
    """Return the default-session CloudWatch client.

    Returns:
        The boto3 CloudWatch client bound to the default session.
    """
    return boto3.client("cloudwatch")


def _duration_seconds(output: AnalyserOutput) -> float:
    """Return the run wall-clock duration."""
    if output.run_ended_at is None:
        return 0.0
    return (output.run_ended_at - output.run_started_at).total_seconds()


def _standard_dims(env: EnvContract) -> list[dict[str, str]]:
    """Return the standard dimension list."""
    return [
        {"Name": "Project", "Value": env.PROJECT_NAME},
        {"Name": "Environment", "Value": env.ENVIRONMENT},
        {"Name": "AnalyserType", "Value": env.ANALYSER_TYPE},
        {"Name": "Variant", "Value": env.VARIANT},
    ]


def build_metric_data(output: AnalyserOutput, env: EnvContract) -> list[dict[str, Any]]:
    """Build the ``MetricData`` list emitted for this run.

    Args:
        output: Analyser output.
        env: Env contract.

    Returns:
        List of metric-data dicts ready for ``put_metric_data``.
    """
    dims = _standard_dims(env)
    timestamp = output.run_started_at
    data: list[dict[str, Any]] = [
        {
            "MetricName": "RunCount",
            "Dimensions": dims,
            "Value": 1,
            "Unit": "Count",
            "Timestamp": timestamp,
        },
        {
            "MetricName": "RunDurationSeconds",
            "Dimensions": dims,
            "Value": _duration_seconds(output),
            "Unit": "Seconds",
            "Timestamp": timestamp,
        },
        {
            "MetricName": "ViolationCount",
            "Dimensions": dims,
            "Value": output.violation_count,
            "Unit": "Count",
            "Timestamp": timestamp,
        },
        {
            "MetricName": "Severity",
            "Dimensions": dims,
            "Value": severity_score(output.resolved_severity().value),
            "Unit": "None",
            "Timestamp": timestamp,
        },
    ]
    for name, value in output.analyser_metrics.items():
        data.append(
            {
                "MetricName": "MetricValue",
                "Dimensions": [*dims, {"Name": "MetricName", "Value": name}],
                "Value": value,
                "Unit": "None",
                "Timestamp": timestamp,
            },
        )
    return data


def emit_standard_metrics(output: AnalyserOutput, env: EnvContract) -> None:
    """Emit the standard + analyser-specific CW metrics for this run.

    Args:
        output: Analyser output.
        env: Env contract.
    """
    _client().put_metric_data(Namespace=NAMESPACE, MetricData=build_metric_data(output, env))
