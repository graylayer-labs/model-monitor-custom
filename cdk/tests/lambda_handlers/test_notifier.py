"""Moto-backed tests for the notifier Lambda handler.

Covers happy path, idempotency, concurrent CCFE race, missing-key skip, and
non-CCFE error propagation.
"""

from __future__ import annotations

import concurrent.futures
import importlib
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

TABLE_NAME = "test-outcomes"


def _make_record(run_id: str, analyser_type: str, outcome: str = "alert") -> dict[str, Any]:
    return {
        "dynamodb": {
            "NewImage": {
                "run_id": {"S": run_id},
                "analyser_type": {"S": analyser_type},
                "outcome": {"S": outcome},
                "severity": {"S": "alert"},
            }
        }
    }


def _create_outcomes_table() -> None:
    ddb = boto3.client("dynamodb", region_name="eu-west-1")
    ddb.create_table(
        TableName=TABLE_NAME,
        AttributeDefinitions=[
            {"AttributeName": "run_id", "AttributeType": "S"},
            {"AttributeName": "analyser_type", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "run_id", "KeyType": "HASH"},
            {"AttributeName": "analyser_type", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.put_item(
        TableName=TABLE_NAME,
        Item={"run_id": {"S": "r1"}, "analyser_type": {"S": "mq"}, "severity": {"S": "alert"}},
    )
    ddb.put_item(
        TableName=TABLE_NAME,
        Item={"run_id": {"S": "r-race"}, "analyser_type": {"S": "dq"}, "severity": {"S": "alert"}},
    )


def _load_handler(monkeypatch: pytest.MonkeyPatch, table: str = TABLE_NAME):
    monkeypatch.setenv("OUTCOMES_TABLE_NAME", table)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    from model_monitor_cdk.lambda_handlers.notifier import handler as mod

    return importlib.reload(mod)


@mock_aws
def test_ac_n1_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-N1: single alert record marks notified=1 and sets the attribute."""
    _create_outcomes_table()
    mod = _load_handler(monkeypatch)
    event = {"Records": [_make_record("r1", "mq")]}

    result = mod.handler(event, None)

    assert result == {"notified": 1}
    ddb = boto3.client("dynamodb", region_name="eu-west-1")
    item = ddb.get_item(TableName=TABLE_NAME, Key={"run_id": {"S": "r1"}, "analyser_type": {"S": "mq"}})["Item"]
    assert "notified" in item


@mock_aws
def test_ac_n2_idempotency_same_record_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-N2: same record delivered twice, only first invocation counts."""
    _create_outcomes_table()
    mod = _load_handler(monkeypatch)
    event = {"Records": [_make_record("r1", "mq")]}

    first = mod.handler(event, None)
    second = mod.handler(event, None)

    assert first == {"notified": 1}
    assert second == {"notified": 0}


@mock_aws
def test_ac_n3_concurrent_ccfe_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-N3: two concurrent invocations — exactly one wins, no exception escapes."""
    _create_outcomes_table()
    mod = _load_handler(monkeypatch)
    event = {"Records": [_make_record("r-race", "dq")]}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(mod.handler, event, None) for _ in range(2)]
        results = [f.result() for f in futures]

    totals = sorted(r["notified"] for r in results)
    assert totals == [0, 1]


@mock_aws
def test_ac_n4_missing_keys_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-N4: records without run_id or analyser_type are skipped, no raise."""
    _create_outcomes_table()
    mod = _load_handler(monkeypatch)
    bad_record: dict[str, Any] = {"dynamodb": {"NewImage": {"outcome": {"S": "alert"}}}}
    event = {"Records": [bad_record]}

    result = mod.handler(event, None)

    assert result == {"notified": 0}


@mock_aws
def test_ac_n5_non_ccfe_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-N5: pointing at a nonexistent table surfaces ResourceNotFoundException."""
    mod = _load_handler(monkeypatch, table="does-not-exist")
    event = {"Records": [_make_record("r1", "mq")]}

    with pytest.raises(ClientError) as exc_info:
        mod.handler(event, None)
    assert exc_info.value.response["Error"]["Code"] == "ResourceNotFoundException"
