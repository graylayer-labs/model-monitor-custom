"""Provenance capture — image digest, git sha, whitelisted env snapshot."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

IMAGE_DIGEST_PATH = Path("/opt/mmc/image_digest")

ENV_WHITELIST: tuple[str, ...] = (
    "PROJECT_NAME",
    "ANALYSER_TYPE",
    "RUN_ID",
    "ENVIRONMENT",
    "VARIANT",
)


def _read_image_digest(path: Path = IMAGE_DIGEST_PATH) -> str:
    """Return the baked image digest, or ``"unknown"``."""
    try:
        return path.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def _env_snapshot(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a whitelisted copy of the environment — never secrets."""
    src = os.environ if env is None else env
    return {k: src[k] for k in ENV_WHITELIST if k in src}


def capture(
    env: dict[str, str] | None = None,
    image_digest_path: Path = IMAGE_DIGEST_PATH,
) -> dict[str, Any]:
    """Return the provenance dict written to ``_provenance.json``.

    Args:
        env: Env mapping. Defaults to ``os.environ``.
        image_digest_path: Path to the baked digest file.

    Returns:
        Dict with ``image_digest``, ``git_sha``, ``env_snapshot``.
    """
    src: dict[str, str] = dict(os.environ) if env is None else env
    return {
        "image_digest": _read_image_digest(image_digest_path),
        "git_sha": src.get("MMC_GIT_SHA", "unknown"),
        "env_snapshot": _env_snapshot(src),
    }
