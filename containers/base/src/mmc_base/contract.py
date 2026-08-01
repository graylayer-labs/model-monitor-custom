"""Pydantic v2 models for the container I/O contract.

Every model uses ``extra="forbid"`` per design 004 (schema evolution) —
unknown fields fail at construction, catching Clarify-shaped drift.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

S3_URI_PATTERN = r"^s3://[^/]+/.*$"

AnalyserType = Literal["mq", "dq", "bias", "explain", "shadow", "baseline"]
Environment = Literal["test", "prod", "dev"]


class Outcome(StrEnum):
    """Run outcome — what happened. See design 007."""

    succeeded = "succeeded"
    succeeded_with_violations = "succeeded_with_violations"
    skipped_no_input = "skipped_no_input"
    skipped_insufficient_data = "skipped_insufficient_data"
    failed_handled = "failed_handled"
    failed_unhandled = "failed_unhandled"


class Severity(StrEnum):
    """Notifier severity — what the operator should do."""

    info = "info"
    warn = "warn"
    alert = "alert"


DEFAULT_SEVERITY: dict[Outcome, Severity] = {
    Outcome.succeeded: Severity.info,
    Outcome.succeeded_with_violations: Severity.warn,
    Outcome.skipped_no_input: Severity.info,
    Outcome.skipped_insufficient_data: Severity.warn,
    Outcome.failed_handled: Severity.warn,
    Outcome.failed_unhandled: Severity.alert,
}


class EnvContract(BaseModel):
    """Validated view of the container's env-var contract.

    Attributes:
        PROJECT_NAME: Project slug.
        RUN_ID: UUID, correlates all outputs of this run.
        ANALYSER_TYPE: Which analyser is running.
        INPUT_URIS_JSON: Raw JSON string mapping input name to S3 URI.
        OUTPUT_URI: S3 prefix under which the container writes outputs.
        CONFIG_URI: S3 URI of the analyser config JSON.
        ENVIRONMENT: Deployment environment.
        VARIANT: Endpoint variant name.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    PROJECT_NAME: str = Field(min_length=1)
    RUN_ID: UUID
    ANALYSER_TYPE: AnalyserType
    INPUT_URIS_JSON: str
    OUTPUT_URI: str = Field(pattern=S3_URI_PATTERN)
    CONFIG_URI: str = Field(pattern=S3_URI_PATTERN)
    ENVIRONMENT: Environment
    VARIANT: str = "AllTraffic"

    @field_validator("INPUT_URIS_JSON")
    @classmethod
    def _validate_input_uris(cls, value: str) -> str:
        """Parse ``INPUT_URIS_JSON`` and check every value is an S3 URI.

        Args:
            value: Raw JSON string.

        Returns:
            The unchanged input on success.

        Raises:
            ValueError: If the JSON is invalid, not an object, or contains
                a non-s3 URI.
        """
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            msg = f"INPUT_URIS_JSON is not valid JSON: {exc}"
            raise ValueError(msg) from exc
        if not isinstance(parsed, dict):
            msg = "INPUT_URIS_JSON must decode to a JSON object"
            raise ValueError(msg)  # ruff: ignore[type-check-without-type-error] — pydantic field_validator wants ValueError
        for name, uri in parsed.items():
            if not isinstance(uri, str) or not uri.startswith("s3://"):
                msg = f"INPUT_URIS_JSON[{name!r}] must be an s3:// URI"
                raise ValueError(msg)
        return value

    @property
    def input_uris(self) -> dict[str, str]:
        """Decoded ``{name: s3_uri}`` mapping."""
        return json.loads(self.INPUT_URIS_JSON)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> EnvContract:
        """Construct from ``os.environ`` (or a supplied mapping).

        Args:
            env: Mapping to read from. Defaults to ``os.environ``.

        Returns:
            Validated :class:`EnvContract`.
        """
        src = os.environ if env is None else env
        required = (
            "PROJECT_NAME",
            "RUN_ID",
            "ANALYSER_TYPE",
            "INPUT_URIS_JSON",
            "OUTPUT_URI",
            "CONFIG_URI",
            "ENVIRONMENT",
        )
        payload: dict[str, Any] = {k: src[k] for k in required if k in src}
        if "VARIANT" in src:
            payload["VARIANT"] = src["VARIANT"]
        return cls.model_validate(payload)


class AnalyserInputs(BaseModel):
    """Local paths of analyser inputs after S3 fetch.

    Attributes:
        paths: ``{name: local_path}`` mapping.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    paths: dict[str, Path] = Field(default_factory=dict)


class AnalyserOutput(BaseModel):
    """Structured return type of every analyser.

    Attributes:
        schema_version: Semver ``"MAJOR.MINOR"`` of the output shape.
        outcome: One of the five analyser-settable outcomes.
        severity: Notifier severity. Defaults are outcome-driven.
        violation_count: Number of baseline violations reported.
        analyser_metrics: Extra CloudWatch metrics keyed by name.
        run_started_at: Wall-clock start (UTC).
        run_ended_at: Wall-clock end (UTC). Set by base after ``compute``.
        payload: Analyser-specific result body written to ``result.json``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    outcome: Outcome
    severity: Severity | None = None
    violation_count: int = Field(default=0, ge=0)
    analyser_metrics: dict[str, float] = Field(default_factory=dict)
    run_started_at: datetime
    run_ended_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def resolved_severity(self) -> Severity:
        """Return explicit severity, or the outcome's default."""
        return self.severity if self.severity is not None else DEFAULT_SEVERITY[self.outcome]


class FailureSidecar(BaseModel):
    """Shape of ``failure.json`` per design 007.

    Attributes:
        schema_version: Semver of this sidecar shape.
        outcome: Always a ``failed_*`` outcome.
        exception_class: Exception type name.
        message: Stringified exception message.
        traceback: Formatted traceback.
        image_digest: Container image digest.
        git_sha: Source commit that built the image.
        env_snapshot: Whitelisted env-var snapshot (no secrets).
        started_at: Wall-clock start (UTC).
        failed_at: Wall-clock failure time (UTC).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    outcome: Outcome
    exception_class: str
    message: str
    traceback: str
    image_digest: str
    git_sha: str
    env_snapshot: dict[str, str]
    started_at: datetime
    failed_at: datetime
