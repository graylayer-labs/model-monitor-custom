"""Contract-test harness — in-memory stubs + entrypoint driver.

Downstream analyser tests import from :mod:`mmc_base.testing`; this module
is the implementation.
"""

from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

from mmc_base import entrypoint, failure, io_cw, io_ddb, io_s3
from mmc_base.contract import AnalyserInputs, AnalyserOutput, Outcome, Severity


@dataclass
class _S3Object:
    """One in-memory S3 object.

    Attributes:
        body: Raw bytes stored for this key.
    """

    body: bytes


@dataclass
class S3Stub:
    """In-memory S3 client double.

    Records every ``put_object`` and serves ``get_object`` / ``download_file``
    from an internal dict.

    Attributes:
        objects: ``{(bucket, key): _S3Object}`` mapping.
        puts: History of ``put_object`` kwargs.
    """

    objects: dict[tuple[str, str], _S3Object] = field(default_factory=dict)
    puts: list[dict[str, Any]] = field(default_factory=list)

    def seed_json(self, bucket: str, key: str, body: dict[str, Any]) -> None:
        """Pre-populate a JSON object for later fetch.

        Args:
            bucket: S3 bucket.
            key: S3 key.
            body: Object body encoded as JSON.
        """
        self.objects[bucket, key] = _S3Object(json.dumps(body).encode("utf-8"))

    def seed_bytes(self, bucket: str, key: str, body: bytes) -> None:
        """Pre-populate a raw-bytes object for later fetch.

        Args:
            bucket: S3 bucket.
            key: S3 key.
            body: Raw object body.
        """
        self.objects[bucket, key] = _S3Object(body)

    def get_object(self, **kwargs: str) -> dict[str, Any]:
        """Return the object matching boto3 kwargs ``Bucket``/``Key``.

        Args:
            **kwargs: boto3-style ``Bucket``/``Key``.

        Returns:
            Dict with ``Body`` file-like matching the boto3 shape.
        """
        bucket, key = kwargs["Bucket"], kwargs["Key"]
        obj = self.objects[bucket, key]
        return {"Body": io.BytesIO(obj.body)}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:  # ruff: ignore[any-type] — boto3 API shape
        """Record the put; store the body.

        Args:
            **kwargs: boto3-style ``Bucket``/``Key``/``Body`` + extras.

        Returns:
            Empty dict — boto3 returns a response envelope.
        """
        bucket, key, body = kwargs["Bucket"], kwargs["Key"], kwargs["Body"]
        self.objects[bucket, key] = _S3Object(body)
        self.puts.append(dict(kwargs))
        return {}

    def download_file(self, *args: str, **kwargs: str) -> None:
        """Materialise the object at the target filename.

        Args:
            *args: Positional ``(Bucket, Key, Filename)`` per boto3.
            **kwargs: Same args in keyword form.
        """
        if args:
            bucket, key, filename = args[0], args[1], args[2]
        else:
            bucket, key, filename = kwargs["Bucket"], kwargs["Key"], kwargs["Filename"]
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        Path(filename).write_bytes(self.objects[bucket, key].body)

    def json_at(self, bucket: str, key: str) -> dict[str, Any]:
        """Return the stored object as parsed JSON.

        Args:
            bucket: S3 bucket.
            key: S3 key.

        Returns:
            Parsed JSON body.
        """
        return json.loads(self.objects[bucket, key].body)


@dataclass
class DDBStub:
    """In-memory DynamoDB client double.

    Attributes:
        put_items: History of ``put_item`` calls.
    """

    put_items: list[dict[str, Any]] = field(default_factory=list)

    def put_item(self, **kwargs: Any) -> dict[str, Any]:  # ruff: ignore[any-type] — boto3 API shape
        """Record the put.

        Args:
            **kwargs: boto3-style ``TableName``/``Item``.

        Returns:
            Empty dict — matches boto3.
        """
        self.put_items.append(dict(kwargs))
        return {}


@dataclass
class CWStub:
    """In-memory CloudWatch client double.

    Attributes:
        calls: History of ``put_metric_data`` calls.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    def put_metric_data(self, **kwargs: Any) -> dict[str, Any]:  # ruff: ignore[any-type] — boto3 API shape
        """Record the put.

        Args:
            **kwargs: boto3-style ``Namespace``/``MetricData``.

        Returns:
            Empty dict — matches boto3.
        """
        self.calls.append(dict(kwargs))
        return {}


class NoopAnalyser:
    """Trivial analyser used by the harness and downstream tests."""

    def compute(self, inputs: AnalyserInputs, config: dict[str, Any]) -> AnalyserOutput:  # ruff: ignore[no-self-use] — protocol conformance
        """Return a fixed successful output.

        Args:
            inputs: Materialised inputs (unused).
            config: Config dict (unused).

        Returns:
            An :class:`AnalyserOutput` with ``outcome=succeeded``.
        """
        now = datetime.now(UTC)
        return AnalyserOutput(
            outcome=Outcome.succeeded,
            severity=Severity.info,
            violation_count=0,
            analyser_metrics={"MetricA": 0.5},
            run_started_at=now,
            payload={"noop": True},
        )


def env_contract_valid(**overrides: str) -> dict[str, str]:
    """Return a valid env-var mapping.

    Args:
        **overrides: Values that replace defaults / add extras.

    Returns:
        Full ``{name: value}`` mapping for :meth:`EnvContract.from_env`.
    """
    env: dict[str, str] = {
        "PROJECT_NAME": "example-classifier",
        "RUN_ID": str(uuid4()),
        "ANALYSER_TYPE": "bias",
        "INPUT_URIS_JSON": json.dumps({"snapshot": "s3://bucket/in/snap.jsonl"}),
        "OUTPUT_URI": "s3://bucket/out/bias",
        "CONFIG_URI": "s3://bucket/in/config.json",
        "ENVIRONMENT": "test",
        "VARIANT": "AllTraffic",
        "OUTCOMES_TABLE_NAME": "mmc-test-outcomes",
        "MMC_GIT_SHA": "deadbeef",
    }
    env.update(overrides)
    return env


def _register_analyser(cls: type) -> str:
    """Attach ``cls`` to this module so ``MMC_ANALYSER_MODULE`` can find it.

    Args:
        cls: Analyser class to register.

    Returns:
        ``"module:ClassName"`` spec that importlib can resolve.
    """
    self_mod = sys.modules[__name__]
    setattr(self_mod, cls.__name__, cls)
    return f"{__name__}:{cls.__name__}"


def _seed_config(s3: S3Stub, uri: str, body: dict[str, Any]) -> None:
    """Seed a config JSON at ``uri``.

    Args:
        s3: The S3 stub.
        uri: S3 URI of the config.
        body: Config body.
    """
    parsed = urlparse(uri)
    s3.seed_json(parsed.netloc, parsed.path.lstrip("/"), body)


def _seed_inputs(s3: S3Stub, uris: dict[str, str], bodies: dict[str, bytes]) -> None:
    """Seed each input URI with the matching body (or empty bytes).

    Args:
        s3: The S3 stub.
        uris: ``{name: s3_uri}``.
        bodies: ``{name: bytes}`` bodies (missing names get ``b""``).
    """
    for name, uri in uris.items():
        parsed = urlparse(uri)
        s3.seed_bytes(parsed.netloc, parsed.path.lstrip("/"), bodies.get(name, b""))


def run_container_flow(
    analyser_cls: type,
    env_overrides: dict[str, str] | None = None,
    *,
    config: dict[str, Any] | None = None,
    input_bodies: dict[str, bytes] | None = None,
    image_digest: str = "sha256:test",
) -> tuple[int, dict[str, Any]]:
    """Drive :func:`mmc_base.entrypoint.main` with all IO stubbed out.

    Args:
        analyser_cls: Analyser class to run. Auto-registered on the harness module.
        env_overrides: Env vars to override on top of :func:`env_contract_valid`.
        config: Config JSON body to seed at ``CONFIG_URI``.
        input_bodies: Optional ``{name: bytes}`` bodies to seed for inputs.
        image_digest: Value returned by the image-digest reader.

    Returns:
        Tuple of ``(exit_code, {"s3": S3Stub, "ddb": DDBStub, "cw": CWStub})``.
    """
    env = env_contract_valid(**(env_overrides or {}))
    module_spec = _register_analyser(analyser_cls)
    env["MMC_ANALYSER_MODULE"] = module_spec

    s3, ddb, cw = S3Stub(), DDBStub(), CWStub()

    cfg = config if config is not None else {}
    _seed_config(s3, env["CONFIG_URI"], cfg)
    _seed_inputs(s3, json.loads(env["INPUT_URIS_JSON"]), input_bodies or {})

    with (
        patch.object(io_s3, "_client", return_value=s3),
        patch.object(io_ddb, "_client", return_value=ddb),
        patch.object(io_cw, "_client", return_value=cw),
        patch.object(failure, "_client", return_value=s3),
        patch.object(
            entrypoint.provenance,
            "capture",
            return_value={"image_digest": image_digest, "git_sha": "deadbeef", "env_snapshot": {}},
        ),
        patch.dict("os.environ", env, clear=True),
    ):
        code = entrypoint.main()

    return code, {"s3": s3, "ddb": ddb, "cw": cw}
