"""Phase 3.5 skeleton shadow analyser.

`NoopShadowAnalyser` returns a canned :class:`AnalyserOutput` so we can
prove the base+analyser plumbing (env contract, IO, DDB, CW, provenance)
extends to the Shadow analyser type without shipping real math. Phase 5
replaces this class with a real challenger-vs-champion drift signal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mmc_base.contract import AnalyserOutput, Outcome, Severity

if TYPE_CHECKING:
    from mmc_base.contract import AnalyserInputs


class NoopShadowAnalyser:
    """Phase 3.5 no-op shadow analyser.

    Returns a fixed successful :class:`AnalyserOutput` to exercise the
    per-analyser metric and payload plumbing under ``analyser_type=shadow``.
    Performs no AWS calls and no Shadow math; Phase 5 supplies the real
    challenger-vs-champion implementation.
    """

    def compute(self, inputs: AnalyserInputs, config: dict[str, Any]) -> AnalyserOutput:  # noqa: PLR6301
        """Return a canned successful output.

        Args:
            inputs: Materialised inputs (unused in the skeleton).
            config: Parsed analyser config (unused in the skeleton).

        Returns:
            An :class:`AnalyserOutput` with ``outcome=succeeded``,
            ``severity=info``, and a per-analyser metric + payload.
        """
        started = datetime.now(UTC)
        ended = datetime.now(UTC)
        return AnalyserOutput(
            outcome=Outcome.succeeded,
            severity=Severity.info,
            violation_count=0,
            analyser_metrics={"NoopDqSignal": 0.0},
            run_started_at=started,
            run_ended_at=ended,
            payload={"note": "NoopShadowAnalyser — real Shadow math in Phase 5"},
        )
