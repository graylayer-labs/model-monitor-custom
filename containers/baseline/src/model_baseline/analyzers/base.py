"""Analyzer protocols consumed by the container entrypoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import pandas as pd

    from model_baseline.adapters.base import ModelAdapter
    from model_baseline.config import BaselineConfig
    from model_baseline.report import AnalysisReport
    from model_baseline.specs import BiasSpec, ExplainSpec


@runtime_checkable
class BiasAnalyzer(Protocol):
    """Computes a pre-training bias report from a spec + data."""

    def compute(
        self,
        config: BaselineConfig,
        spec: BiasSpec,
        data: pd.DataFrame,
    ) -> AnalysisReport:
        """Produce an :class:`AnalysisReport` for the given spec + data."""
        ...


@runtime_checkable
class ExplainabilityAnalyzer(Protocol):
    """Computes a Kernel SHAP explainability report using a model adapter."""

    def compute(
        self,
        config: BaselineConfig,
        spec: ExplainSpec,
        data: pd.DataFrame,
        adapter: ModelAdapter,
    ) -> AnalysisReport:
        """Produce an :class:`AnalysisReport` for SHAP explanations."""
        ...
