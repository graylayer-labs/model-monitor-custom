"""mmc-base — shared analyser base package.

Owns the container I/O contract (env vars, S3/DDB/CloudWatch), the failure
sidecar, and the SageMaker ban-list guard. Analyser images extend this and
implement only :class:`Analyser`.
"""

from __future__ import annotations

from mmc_base.analyser import Analyser
from mmc_base.contract import (
    AnalyserInputs,
    AnalyserOutput,
    EnvContract,
    FailureSidecar,
    Outcome,
    Severity,
)

__all__ = [
    "Analyser",
    "AnalyserInputs",
    "AnalyserOutput",
    "EnvContract",
    "FailureSidecar",
    "Outcome",
    "Severity",
]
