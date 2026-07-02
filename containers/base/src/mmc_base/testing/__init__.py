"""Reusable contract-test harness for analyser images.

Exposes stubs for S3 / DDB / CloudWatch, a :class:`NoopAnalyser`, and a
one-call :func:`run_container_flow` that drives the base entrypoint with
all IO mocked out.
"""

from __future__ import annotations

from mmc_base.testing.harness import (
    CWStub,
    DDBStub,
    NoopAnalyser,
    S3Stub,
    env_contract_valid,
    run_container_flow,
)

__all__ = [
    "CWStub",
    "DDBStub",
    "NoopAnalyser",
    "S3Stub",
    "env_contract_valid",
    "run_container_flow",
]
