"""Phase 3.4 skeleton MQ analyser.

`NoopMqAnalyser` returns a canned :class:`AnalyserOutput` so we can
prove the base+analyser pattern extends beyond bias (env contract, IO,
DDB, CW, provenance) without shipping MQ math. Phase 5 replaces this
class with the real model-quality implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mmc_base.contract import AnalyserOutput, Outcome, Severity

if TYPE_CHECKING:
    from mmc_base.contract import AnalyserInputs


class NoopMqAnalyser:
    """Phase 3.4 no-op MQ analyser.

    Returns a fixed successful :class:`AnalyserOutput` to exercise the
    per-analyser metric and payload plumbing. Performs no AWS calls and
    no MQ math; Phase 5 supplies the real implementation.
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
            payload={"note": "NoopMqAnalyser — real MQ math in Phase 5"},
        )
