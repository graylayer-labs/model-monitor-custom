"""Smoke tests for analyser Dockerfiles.

Each analyser must accept ``BASE_IMAGE`` as a build arg so
``scripts/build-and-push-analysers.sh`` can thread the just-pushed
``analyser-base:$SHA`` in. A hardcoded ECR host in ``FROM`` would defeat that
and make first-deploy require a manual Dockerfile edit — the exact pain this
squad is here to remove.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ANALYSERS = ("bias", "dq", "explain", "mq", "shadow")
_ECR_HOST_PATTERN = re.compile(r"\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com")


@pytest.mark.parametrize("analyser", _ANALYSERS)
def test_analyser_dockerfile_declares_base_image_arg(analyser: str) -> None:
    """Every analyser Dockerfile must declare ``ARG BASE_IMAGE`` before ``FROM``."""
    dockerfile = _REPO_ROOT / "containers" / analyser / "Dockerfile"
    contents = dockerfile.read_text(encoding="utf-8")
    assert "ARG BASE_IMAGE" in contents, f"{dockerfile} must declare ARG BASE_IMAGE"
    assert "FROM ${BASE_IMAGE}" in contents, f"{dockerfile} must use FROM ${{BASE_IMAGE}}"


@pytest.mark.parametrize("analyser", _ANALYSERS)
def test_analyser_dockerfile_has_no_hardcoded_ecr_host(analyser: str) -> None:
    """Reject a hardcoded ``<acct>.dkr.ecr.<region>.amazonaws.com`` in the FROM line."""
    dockerfile = _REPO_ROOT / "containers" / analyser / "Dockerfile"
    contents = dockerfile.read_text(encoding="utf-8")
    match = _ECR_HOST_PATTERN.search(contents)
    assert match is None, f"{dockerfile} contains hardcoded ECR host {match.group(0) if match else ''!r}"
