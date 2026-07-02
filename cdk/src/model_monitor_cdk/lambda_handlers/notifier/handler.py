"""Notifier Lambda — logs one line per alert-severity outcome, idempotent.

Triggered by an EventBridge Pipe filtering the outcomes DDB stream on
``severity == "alert"``. Real Slack/PagerDuty wiring is deferred; this
handler exists so the pipe target is a real Lambda and the idempotency
guard is enforced from day one.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_OUTCOMES_TABLE = os.environ.get("OUTCOMES_TABLE_NAME", "")
_ddb = boto3.client("dynamodb")


def _extract(new_image: dict[str, Any], key: str) -> str:
    """Pull a string attribute out of a DDB ``NewImage`` block.

    Returns:
        Value of the ``S`` field, empty string if absent.
    """
    raw = new_image.get(key, {})
    return str(raw.get("S", "")) if isinstance(raw, dict) else ""


def _mark_notified(run_id: str, analyser_type: str) -> bool:
    """Write ``notified`` on the outcomes row iff absent.

    Returns:
        True if this call marked the row, False if another invocation
        already claimed it.

    Raises:
        ClientError: On any DDB error other than a conditional check failure.
    """
    now = _dt.datetime.now(tz=_dt.UTC).isoformat()
    try:
        _ddb.update_item(
            TableName=_OUTCOMES_TABLE,
            Key={"run_id": {"S": run_id}, "analyser_type": {"S": analyser_type}},
            UpdateExpression="SET notified = :n",
            ConditionExpression="attribute_not_exists(notified)",
            ExpressionAttributeValues={":n": {"S": now}},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise
    return True


def handler(event: dict[str, Any], _context: object) -> dict[str, int]:
    """Log an alert line per pipe record, guarded by idempotent DDB write.

    Returns:
        Dict with ``notified`` = count of first-time alerts emitted.
    """
    records = event if isinstance(event, list) else event.get("Records", [event])
    notified = 0
    for record in records:
        new_image = record.get("dynamodb", {}).get("NewImage", {}) if isinstance(record, dict) else {}
        run_id = _extract(new_image, "run_id")
        analyser_type = _extract(new_image, "analyser_type")
        outcome = _extract(new_image, "outcome")
        if not run_id or not analyser_type:
            logger.warning("skipping record missing keys: %s", json.dumps(record)[:400])
            continue
        if not _mark_notified(run_id, analyser_type):
            logger.info("[NOTIFY-SKIP] already notified run_id=%s analyser=%s", run_id, analyser_type)
            continue
        logger.info("[NOTIFY] run_id=%s analyser=%s outcome=%s", run_id, analyser_type, outcome)
        notified += 1
    return {"notified": notified}
