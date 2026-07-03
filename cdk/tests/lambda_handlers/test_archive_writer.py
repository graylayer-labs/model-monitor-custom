"""Moto-backed tests for the archive_writer Lambda handler."""

from __future__ import annotations

import importlib
import json
import re
from typing import Any

import boto3
import pytest
from moto import mock_aws

BUCKET = "test-archive"

KEY_RE = re.compile(r"^execution-outcomes-archive/dt=\d{4}-\d{2}-\d{2}/hour=\d{2}/[A-Za-z0-9\-]+\.json$")


def _create_bucket() -> None:
    s3 = boto3.client("s3", region_name="eu-west-1")
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})


def _load_handler(monkeypatch: pytest.MonkeyPatch, bucket: str = BUCKET):
    monkeypatch.setenv("ARCHIVE_BUCKET", bucket)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    from model_monitor_cdk.lambda_handlers.archive_writer import handler as mod

    return importlib.reload(mod)


def _record(event_id: str) -> dict[str, Any]:
    return {
        "eventID": event_id,
        "eventName": "INSERT",
        "dynamodb": {"NewImage": {"run_id": {"S": "r1"}}},
    }


def _list_keys() -> list[str]:
    s3 = boto3.client("s3", region_name="eu-west-1")
    resp = s3.list_objects_v2(Bucket=BUCKET)
    return sorted(o["Key"] for o in resp.get("Contents", []))


@mock_aws
def test_ac_a1_happy_path_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-A1: batch of two records → two S3 objects, keys match partition, bodies parseable."""
    _create_bucket()
    mod = _load_handler(monkeypatch)
    event = {"Records": [_record("evt-1"), _record("evt-2")]}

    result = mod.handler(event, None)

    assert result == {"archived": 2}
    keys = _list_keys()
    assert len(keys) == 2
    for k in keys:
        assert KEY_RE.match(k), k
    s3 = boto3.client("s3", region_name="eu-west-1")
    body = json.loads(s3.get_object(Bucket=BUCKET, Key=keys[0])["Body"].read())
    assert body["eventID"] in {"evt-1", "evt-2"}


@mock_aws
def test_ac_a2_single_record_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-A2: event is a bare dict (no Records list) — handler wraps it, writes one object."""
    _create_bucket()
    mod = _load_handler(monkeypatch)
    event = _record("solo-1")

    result = mod.handler(event, None)

    assert result == {"archived": 1}
    keys = _list_keys()
    assert len(keys) == 1
    assert "solo-1.json" in keys[0]


@mock_aws
def test_ac_a3_missing_event_id_uses_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-A3: record without eventID → UUID filename, write still succeeds."""
    _create_bucket()
    mod = _load_handler(monkeypatch)
    event = {"Records": [{"eventName": "INSERT", "dynamodb": {"NewImage": {}}}]}

    result = mod.handler(event, None)

    assert result == {"archived": 1}
    keys = _list_keys()
    assert len(keys) == 1
    assert KEY_RE.match(keys[0])


@mock_aws
def test_ac_a4_content_type_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-A4: PUT sets application/json content type."""
    _create_bucket()
    mod = _load_handler(monkeypatch)
    mod.handler({"Records": [_record("evt-ct")]}, None)

    s3 = boto3.client("s3", region_name="eu-west-1")
    key = _list_keys()[0]
    head = s3.head_object(Bucket=BUCKET, Key=key)
    assert head["ContentType"] == "application/json"
