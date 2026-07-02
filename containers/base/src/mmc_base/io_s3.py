"""S3 helpers — fetch inputs / config, write result + provenance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import boto3
from botocore.client import BaseClient

if TYPE_CHECKING:
    from mmc_base.contract import AnalyserOutput


def _split_s3_uri(uri: str) -> tuple[str, str]:
    """Return ``(bucket, key)`` for an ``s3://bucket/key`` URI.

    Args:
        uri: Full S3 URI.

    Returns:
        Tuple of bucket and key.

    Raises:
        ValueError: If ``uri`` is not an s3 URI or has no key.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        msg = f"not an s3 URI: {uri}"
        raise ValueError(msg)
    key = parsed.path.lstrip("/")
    if not key:
        msg = f"s3 URI missing key: {uri}"
        raise ValueError(msg)
    return parsed.netloc, key


def _client() -> BaseClient:
    """Return the default-session S3 client.

    Returns:
        The boto3 S3 client bound to the default session.
    """
    return boto3.client("s3")


def fetch_config(uri: str) -> dict[str, Any]:
    """Fetch a JSON config from S3.

    Args:
        uri: ``s3://bucket/key`` of the config JSON.

    Returns:
        Parsed JSON as a dict.
    """
    bucket, key = _split_s3_uri(uri)
    body = _client().get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


def fetch_inputs(uris: dict[str, str], to_dir: Path) -> dict[str, Path]:
    """Download input objects to ``to_dir``.

    Args:
        uris: ``{name: s3_uri}`` mapping.
        to_dir: Local directory (created if missing).

    Returns:
        ``{name: local_path}`` for every fetched input.
    """
    to_dir.mkdir(parents=True, exist_ok=True)
    client = _client()
    paths: dict[str, Path] = {}
    for name, uri in uris.items():
        bucket, key = _split_s3_uri(uri)
        target = to_dir / f"{name}__{Path(key).name}"
        client.download_file(bucket, key, str(target))
        paths[name] = target
    return paths


def _put_json(uri: str, body: dict[str, Any]) -> None:
    """Put a JSON document at ``uri``."""
    bucket, key = _split_s3_uri(uri)
    _client().put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(body, default=str).encode("utf-8"),
        ContentType="application/json",
    )


def write_output(output: AnalyserOutput, base_uri: str, provenance: dict[str, Any]) -> None:
    """Write ``result.json`` and ``_provenance.json`` under ``base_uri``.

    Args:
        output: Analyser output.
        base_uri: S3 prefix (``s3://bucket/prefix``).
        provenance: Provenance dict from :func:`mmc_base.provenance.capture`.
    """
    result_body = {
        "schema_version": output.schema_version,
        "outcome": output.outcome.value,
        "severity": output.resolved_severity().value,
        "violation_count": output.violation_count,
        "analyser_metrics": output.analyser_metrics,
        "run_started_at": output.run_started_at.isoformat(),
        "run_ended_at": output.run_ended_at.isoformat() if output.run_ended_at else None,
        "payload": output.payload,
    }
    provenance_body = {
        "schema_version": "1.0",
        **provenance,
        "run_started_at": output.run_started_at.isoformat(),
        "run_ended_at": output.run_ended_at.isoformat() if output.run_ended_at else None,
    }
    base = base_uri.rstrip("/")
    _put_json(f"{base}/result.json", result_body)
    _put_json(f"{base}/_provenance.json", provenance_body)
