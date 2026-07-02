"""Archive Lambda — writes every outcomes DDB stream event to S3.

Target of the ``mmc-<env>-outcome-archive`` EventBridge Pipe. No filter on
the source pipe — this is the full audit log per 007 (failure taxonomy).
Objects land at ``s3://<archive>/execution-outcomes-archive/dt=YYYY-MM-DD/hour=HH/<event_id>.json``.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import uuid
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_ARCHIVE_BUCKET = os.environ.get("ARCHIVE_BUCKET", "")
_s3 = boto3.client("s3")


def _partition_key(event_id: str, now: _dt.datetime) -> str:
    """Hive-style partitioned key: dt/hour partitions + event id filename.

    Returns:
        S3 object key for the archive object.
    """
    return f"execution-outcomes-archive/dt={now.strftime('%Y-%m-%d')}/hour={now.strftime('%H')}/{event_id}.json"


def _archive_record(record: dict[str, Any]) -> None:
    """Serialize a single DDB stream record and PUT it to S3."""
    event_id = str(record.get("eventID") or uuid.uuid4())
    now = _dt.datetime.now(tz=_dt.UTC)
    key = _partition_key(event_id, now)
    body = json.dumps(record, default=str).encode("utf-8")
    _s3.put_object(Bucket=_ARCHIVE_BUCKET, Key=key, Body=body, ContentType="application/json")
    logger.info("[ARCHIVE] wrote s3://%s/%s (%d bytes)", _ARCHIVE_BUCKET, key, len(body))


def handler(event: dict[str, Any], _context: object) -> dict[str, int]:
    """Fan pipe records out to individual S3 objects.

    Returns:
        Dict with ``archived`` = number of records written.
    """
    records = event if isinstance(event, list) else event.get("Records", [event])
    for record in records:
        _archive_record(record if isinstance(record, dict) else {"raw": record})
    return {"archived": len(records)}
