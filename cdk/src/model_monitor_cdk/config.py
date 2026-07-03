"""Config-driven topology loader for the MMC CDK app.

Two YAML files describe the deployment topology:

- ``accounts.yaml`` — which AWS account plays which role
  (``artifact`` / ``operations`` / ``inference[]``), plus the region.
- ``projects.yaml`` — the list of projects (models) and which inference
  account each one lives in.

Single-account collapse works by listing the same 12-digit account ID
under all three roles. Stack code is topology-agnostic — CDK filters
stacks by ``env.account`` against the deploying profile.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

if TYPE_CHECKING:
    import aws_cdk as cdk

_ACCOUNT_ID_PATTERN = re.compile(r"^\d{12}$")
_S3_BUCKET_ARN_PATTERN = re.compile(r"^arn:aws:s3:::[a-z0-9][a-z0-9.-]*[a-z0-9]$")
_DEFAULT_SCHEDULE = "cron(0 * * * ? *)"


def _validate_account_id(value: object, info: ValidationInfo) -> str:
    r"""Reject int account IDs and enforce 12-digit format.

    Args:
        value: Candidate account ID as provided by pydantic.
        info: Pydantic validation context (used for field name in error).

    Returns:
        The validated 12-digit account ID string.

    Raises:
        ValueError: If ``value`` is not a str matching ``^\d{12}$``.
    """
    if not isinstance(value, str):
        msg = f"{info.field_name} must be a string, got {type(value).__name__}: {value!r}"
        raise ValueError(msg)  # noqa: TRY004  pydantic wraps as ValidationError
    if not _ACCOUNT_ID_PATTERN.match(value):
        msg = rf"{info.field_name} must match ^\d{{12}}$, got: {value!r}"
        raise ValueError(msg)
    return value


class RolesConfig(BaseModel):
    """Which account plays which topology role.

    Attributes:
        artifact: 12-digit ID of the account that owns the baselines bucket,
            KMS key, and SharedIam roles.
        operations: 12-digit ID of the account that runs baseline snapshot jobs.
        inference: One or more 12-digit inference-account IDs (one per silo).
    """

    model_config = ConfigDict(extra="forbid")

    artifact: str
    operations: str
    inference: list[str] = Field(min_length=1)

    _validate_artifact = field_validator("artifact", mode="before")(_validate_account_id)
    _validate_operations = field_validator("operations", mode="before")(_validate_account_id)

    @field_validator("inference", mode="before")
    @classmethod
    def _validate_inference(cls, value: object) -> list[str]:
        """Validate every inference-account ID is a 12-digit string.

        Args:
            value: Raw value from YAML (expected to be a list).

        Returns:
            The validated list of account IDs.

        Raises:
            ValueError: If ``value`` isn't a list or any entry is malformed.
        """
        if not isinstance(value, list):
            msg = f"inference must be a list of 12-digit strings, got: {value!r}"
            raise ValueError(msg)  # noqa: TRY004
        entries: list[str] = []
        for entry in value:
            if not isinstance(entry, str) or not _ACCOUNT_ID_PATTERN.match(entry):
                msg = rf"inference entries must match ^\d{{12}}$, got: {entry!r}"
                raise ValueError(msg)
            entries.append(entry)
        if len(set(entries)) != len(entries):
            msg = f"inference entries must be unique, got: {value!r}"
            raise ValueError(msg)
        return entries


class AccountsConfig(BaseModel):
    """Top-level accounts.yaml shape.

    Attributes:
        region: AWS region every stack deploys into (single-region prototype).
        roles: Which account plays which topology role.
        operations_vpc_id: Optional VPC ID for the ml-operations stack to
            adopt (single-account collapse). ``None`` → stack creates its own.
    """

    model_config = ConfigDict(extra="forbid")

    region: str
    roles: RolesConfig
    operations_vpc_id: str | None = None


class ProjectSpec(BaseModel):
    """One monitored project (model / silo).

    Attributes:
        name: Project slug (used in stack IDs and tags).
        inference_account: 12-digit ID of the inference account this project
            runs in. Must appear in ``AccountsConfig.roles.inference``.
        producer_bucket_arn: ARN of the S3 bucket owned by the producer team
            that this project's training snapshots land in. Used by
            :class:`OperationsBaselineStack` for read grants and the
            EventBridge object-created rule.
        schedule: EventBridge Scheduler cron expression for the tick.
        vpc_id: Optional VPC ID for the inference stack to adopt.
            ``None`` → stack creates its own VPC.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    inference_account: str
    producer_bucket_arn: str
    schedule: str = _DEFAULT_SCHEDULE
    vpc_id: str | None = None

    _validate_inference_account = field_validator("inference_account", mode="before")(_validate_account_id)

    @field_validator("producer_bucket_arn", mode="before")
    @classmethod
    def _validate_producer_bucket_arn(cls, value: object) -> str:
        """Enforce non-empty S3 bucket ARN.

        Args:
            value: Raw ARN from YAML.

        Returns:
            The validated ARN string.

        Raises:
            ValueError: If ``value`` isn't a matching S3 bucket ARN.
        """
        if not isinstance(value, str) or not value:
            msg = f"producer_bucket_arn must be a non-empty string, got: {value!r}"
            raise ValueError(msg)
        if not _S3_BUCKET_ARN_PATTERN.match(value):
            msg = f"producer_bucket_arn must match arn:aws:s3:::<bucket>, got: {value!r}"
            raise ValueError(msg)
        return value


class ProjectsConfig(BaseModel):
    """Top-level projects.yaml shape.

    Attributes:
        projects: One :class:`ProjectSpec` per model / silo.
    """

    model_config = ConfigDict(extra="forbid")

    projects: list[ProjectSpec] = Field(min_length=1)


class EnvConfig(BaseModel):
    """Bundled accounts + projects config, cross-validated.

    Attributes:
        accounts: Loaded ``accounts.yaml`` content.
        projects: Loaded ``projects.yaml`` content.
    """

    model_config = ConfigDict(extra="forbid")

    accounts: AccountsConfig
    projects: ProjectsConfig

    @model_validator(mode="after")
    def _cross_ref_inference_accounts(self) -> EnvConfig:
        """Every project must point at a known inference account.

        Returns:
            ``self`` unchanged on success.

        Raises:
            ValueError: If any project references an account not listed in
                ``accounts.roles.inference``.
        """
        known = set(self.accounts.roles.inference)
        for project in self.projects.projects:
            if project.inference_account not in known:
                msg = (
                    f"project {project.name!r} references inference_account "
                    f"{project.inference_account!r} not in accounts.roles.inference={sorted(known)}"
                )
                raise ValueError(msg)
        return self


def _load_yaml(path: Path) -> dict:
    """Read a YAML file and return the top-level mapping.

    Args:
        path: Filesystem path to the YAML file.

    Returns:
        Parsed top-level mapping.

    Raises:
        FileNotFoundError: If the path does not exist.
        ValueError: If YAML parse fails or the top-level isn't a mapping.
    """
    if not path.exists():
        msg = f"config file not found: {path}"
        raise FileNotFoundError(msg)
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"failed to parse YAML at {path}: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(parsed, dict):
        msg = f"expected a top-level mapping in {path}, got: {type(parsed).__name__}"
        raise ValueError(msg)  # noqa: TRY004
    return parsed


def load_env(accounts_path: Path, projects_path: Path) -> EnvConfig:
    """Load and validate the two YAML files into a single :class:`EnvConfig`.

    Args:
        accounts_path: Path to ``accounts.yaml``.
        projects_path: Path to ``projects.yaml``.

    Returns:
        Validated bundle.

    Raises:
        ValueError: If either file fails to parse or validate.
    """
    accounts_raw = _load_yaml(accounts_path)
    projects_raw = _load_yaml(projects_path)
    try:
        accounts = AccountsConfig(**accounts_raw)
    except Exception as exc:
        msg = f"invalid accounts config at {accounts_path}: {exc}"
        raise ValueError(msg) from exc
    try:
        projects = ProjectsConfig(**projects_raw)
    except Exception as exc:
        msg = f"invalid projects config at {projects_path}: {exc}"
        raise ValueError(msg) from exc
    return EnvConfig(accounts=accounts, projects=projects)


def resolve_env_from_context(app: cdk.App) -> EnvConfig:
    """Resolve the ``accounts`` + ``projects`` context keys and load the config.

    Args:
        app: The CDK ``App`` — its ``node.try_get_context`` is consulted.

    Returns:
        Validated :class:`EnvConfig`.

    Raises:
        ValueError: If context is missing or the referenced files fail to load.
    """
    accounts_ctx = app.node.try_get_context("accounts")
    projects_ctx = app.node.try_get_context("projects")
    if not accounts_ctx or not projects_ctx:
        msg = (
            "cdk context is missing 'accounts' and/or 'projects'; "
            "set defaults in cdk.json or pass -c accounts=<path> -c projects=<path>"
        )
        raise ValueError(msg)
    return load_env(Path(accounts_ctx), Path(projects_ctx))
