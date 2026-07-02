"""Failure sidecar — writes ``failure.json`` under ``OUTPUT_URI`` on any failed_* outcome."""

from __future__ import annotations

import json
import traceback as _tb
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import boto3
from botocore.client import BaseClient

from mmc_base.contract import FailureSidecar, Outcome
from mmc_base.io_s3 import _split_s3_uri
from mmc_base.provenance import capture as capture_provenance

if TYPE_CHECKING:
    from mmc_base.contract import EnvContract


def _client() -> BaseClient:
    """Return the default-session S3 client.

    Returns:
        The boto3 S3 client bound to the default session.
    """
    return boto3.client("s3")


def build_sidecar(
    exc: BaseException,
    env: EnvContract,
    started_at: datetime,
    *,
    outcome: Outcome = Outcome.failed_unhandled,
    provenance: dict[str, Any] | None = None,
) -> FailureSidecar:
    """Build a :class:`FailureSidecar` for ``exc``.

    Args:
        exc: The exception being recorded.
        env: Env contract.
        started_at: Wall-clock start of the run.
        outcome: ``failed_unhandled`` (default) or ``failed_handled``.
        provenance: Pre-captured provenance. Captured fresh if absent.

    Returns:
        Validated sidecar model.
    """
    prov = provenance if provenance is not None else capture_provenance()
    return FailureSidecar(
        outcome=outcome,
        exception_class=exc.__class__.__name__,
        message=str(exc),
        traceback="".join(_tb.format_exception(type(exc), exc, exc.__traceback__)),
        image_digest=str(prov.get("image_digest", "unknown")),
        git_sha=str(prov.get("git_sha", "unknown")),
        env_snapshot=prov.get("env_snapshot", {}),
        started_at=started_at,
        failed_at=datetime.now(UTC),
    )


def write_failure(  # noqa: PLR0913 — public API: exc/env/output_uri/started_at + outcome/provenance
    exc: BaseException,
    env: EnvContract,
    output_uri: str,
    started_at: datetime,
    *,
    outcome: Outcome = Outcome.failed_unhandled,
    provenance: dict[str, Any] | None = None,
) -> tuple[FailureSidecar, str]:
    """Serialise a :class:`FailureSidecar` to ``<output_uri>/failure.json``.

    Args:
        exc: The exception being recorded.
        env: Env contract.
        output_uri: S3 prefix (``s3://bucket/prefix``).
        started_at: Wall-clock start of the run.
        outcome: ``failed_unhandled`` (default) or ``failed_handled``.
        provenance: Pre-captured provenance. Captured fresh if absent.

    Returns:
        Tuple of ``(sidecar, failure_uri)``.
    """
    sidecar = build_sidecar(exc, env, started_at, outcome=outcome, provenance=provenance)
    failure_uri = f"{output_uri.rstrip('/')}/failure.json"
    bucket, key = _split_s3_uri(failure_uri)
    _client().put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(sidecar.model_dump(mode="json"), default=str).encode("utf-8"),
        ContentType="application/json",
    )
    return sidecar, failure_uri
