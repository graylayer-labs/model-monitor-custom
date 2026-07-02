"""Container CLI helpers.

Phase 1: only the env-var-to-config resolver. The actual entrypoint
(main function that runs the analyzer) lands in Phase 2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

from model_baseline.config import BaselineConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

_REQUIRED_ENV = (
    "PACKAGE_GROUP_NAME",
    "BASELINE_VERSION",
    "MONITOR_TYPE",
    "INPUT_S3_URI",
    "CONFIG_S3_URI",
    "OUTPUT_S3_URI",
)


def resolve_config_from_env(env: Mapping[str, str]) -> BaselineConfig:
    """Build a :class:`BaselineConfig` from environment variables.

    Args:
        env: Mapping of env vars (typically ``os.environ``).

    Returns:
        Validated :class:`BaselineConfig`.

    Raises:
        KeyError: If a required env var is missing. Message names the var.
    """
    for key in _REQUIRED_ENV:
        if key not in env:
            msg = f"Required env var missing: {key}"
            raise KeyError(msg)

    return BaselineConfig(
        package_group_name=env["PACKAGE_GROUP_NAME"],
        baseline_version=int(env["BASELINE_VERSION"]),
        monitor_type=cast("Literal['BIAS', 'EXPLAINABILITY']", env["MONITOR_TYPE"]),
        input_s3_uri=env["INPUT_S3_URI"],
        config_s3_uri=env["CONFIG_S3_URI"],
        output_s3_uri=env["OUTPUT_S3_URI"],
        model_s3_uri=env.get("MODEL_S3_URI"),
    )
