from __future__ import annotations

import json
from uuid import uuid4

import pytest
from mmc_base.contract import EnvContract
from mmc_base.testing import env_contract_valid
from pydantic import ValidationError


def test_valid_env_parses():
    env = env_contract_valid()
    contract = EnvContract.from_env(env)
    assert contract.PROJECT_NAME == "example-classifier"
    assert contract.ANALYSER_TYPE == "bias"
    assert contract.VARIANT == "AllTraffic"
    assert contract.input_uris == {"snapshot": "s3://bucket/in/snap.jsonl"}


REQUIRED_FIELDS = (
    "PROJECT_NAME",
    "RUN_ID",
    "ANALYSER_TYPE",
    "INPUT_URIS_JSON",
    "OUTPUT_URI",
    "CONFIG_URI",
    "ENVIRONMENT",
)


@pytest.mark.parametrize("field", REQUIRED_FIELDS, ids=REQUIRED_FIELDS)
def test_missing_required_env_raises(field: str) -> None:
    """Each required field, dropped individually, must fail with a message naming it."""
    env = env_contract_valid()
    env.pop(field)
    with pytest.raises(ValidationError, match=field):
        EnvContract.from_env(env)


def test_extra_env_var_ignored_by_from_env() -> None:
    """`from_env` filters to known keys — pin so a future refactor to pass-through is caught."""
    env = env_contract_valid()
    env["TOTALLY_UNRELATED"] = "x"
    contract = EnvContract.from_env(env)
    assert contract.PROJECT_NAME == "example-classifier"


def test_extra_field_forbidden():
    payload = {
        "PROJECT_NAME": "p",
        "RUN_ID": str(uuid4()),
        "ANALYSER_TYPE": "bias",
        "INPUT_URIS_JSON": json.dumps({}),
        "OUTPUT_URI": "s3://b/out",
        "CONFIG_URI": "s3://b/cfg.json",
        "ENVIRONMENT": "test",
        "NOPE": "x",
    }
    with pytest.raises(ValidationError):
        EnvContract.model_validate(payload)


def test_variant_defaults():
    env = env_contract_valid()
    env.pop("VARIANT")
    contract = EnvContract.from_env(env)
    assert contract.VARIANT == "AllTraffic"


def test_bad_analyser_type_rejected():
    env = env_contract_valid(ANALYSER_TYPE="quality")
    with pytest.raises(ValidationError):
        EnvContract.from_env(env)


def test_bad_run_id_uuid_rejected():
    env = env_contract_valid(RUN_ID="not-a-uuid")
    with pytest.raises(ValidationError):
        EnvContract.from_env(env)


def test_bad_output_uri_rejected():
    env = env_contract_valid(OUTPUT_URI="not-s3://")
    with pytest.raises(ValidationError):
        EnvContract.from_env(env)


def test_bad_input_uris_json_rejected():
    env = env_contract_valid(INPUT_URIS_JSON="not-json")
    with pytest.raises(ValidationError):
        EnvContract.from_env(env)


def test_bad_input_uri_value_rejected():
    env = env_contract_valid(INPUT_URIS_JSON=json.dumps({"snap": "not-s3"}))
    with pytest.raises(ValidationError):
        EnvContract.from_env(env)


def test_input_uris_json_non_object_rejected():
    """Valid JSON but not an object (e.g., a list) → ValidationError."""
    env = env_contract_valid(INPUT_URIS_JSON=json.dumps(["s3://b/x"]))
    with pytest.raises(ValidationError, match="INPUT_URIS_JSON"):
        EnvContract.from_env(env)
