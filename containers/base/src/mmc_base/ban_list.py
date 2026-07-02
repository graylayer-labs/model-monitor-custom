"""SageMaker ban-list guard — guardrail #1 from design 003.

Refuses to run if the container was launched with SageMaker-shaped env
vars or filesystem paths. Kills accidental Processing Job reuse loud
and immediately.
"""

from __future__ import annotations

import os
from pathlib import Path

BANNED_ENV_PREFIXES: tuple[str, ...] = ("SM_", "SAGEMAKER_")
BANNED_PATHS: tuple[str, ...] = ("/opt/ml/",)


class SageMakerContaminationError(RuntimeError):
    """Raised when SageMaker-shaped env vars or paths are present."""


def _banned_env_keys(env: dict[str, str]) -> list[str]:
    """Return env keys that match a banned prefix."""
    return sorted(k for k in env if k.startswith(BANNED_ENV_PREFIXES))


def _banned_paths_present() -> list[str]:
    """Return banned filesystem paths that currently exist."""
    return [p for p in BANNED_PATHS if Path(p).exists()]


def assert_clean_env(env: dict[str, str] | None = None) -> None:
    """Raise if any banned env-var prefix or filesystem path is present.

    Args:
        env: Mapping to check. Defaults to ``os.environ``.

    Raises:
        SageMakerContaminationError: If any banned marker is found.
    """
    src = dict(os.environ) if env is None else env
    bad_keys = _banned_env_keys(src)
    bad_paths = _banned_paths_present()
    if bad_keys or bad_paths:
        msg = (
            "SageMaker contamination detected — this is model-monitor-custom, reshape the caller. "
            f"env={bad_keys} paths={bad_paths}"
        )
        raise SageMakerContaminationError(msg)
