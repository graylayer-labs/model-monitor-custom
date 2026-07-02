"""The :class:`Analyser` Protocol — the one interface every analyser implements."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from mmc_base.contract import AnalyserInputs, AnalyserOutput


@runtime_checkable
class Analyser(Protocol):
    """Pure-function analyser interface.

    Implementations must not perform AWS SDK calls; the base image owns
    all IO. An analyser receives already-fetched inputs plus a config
    dict and returns a validated :class:`AnalyserOutput`.
    """

    def compute(self, inputs: AnalyserInputs, config: dict[str, Any]) -> AnalyserOutput:
        """Compute this analyser's result.

        Args:
            inputs: Locally-materialised input file paths.
            config: Parsed analyser config (fetched from ``CONFIG_URI``).

        Returns:
            The analyser's structured output.
        """
        ...
