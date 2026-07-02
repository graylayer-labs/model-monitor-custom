"""RED tests for analyzer protocols."""

from __future__ import annotations

import pandas as pd
from model_baseline.adapters.base import ModelAdapter
from model_baseline.analyzers.base import BiasAnalyzer, ExplainabilityAnalyzer
from model_baseline.config import BaselineConfig
from model_baseline.report import AnalysisReport
from model_baseline.specs import BiasSpec, ExplainSpec


class GoodBias:
    def compute(self, config: BaselineConfig, spec: BiasSpec, data: pd.DataFrame) -> AnalysisReport:
        raise NotImplementedError


class GoodExplain:
    def compute(
        self,
        config: BaselineConfig,
        spec: ExplainSpec,
        data: pd.DataFrame,
        adapter: ModelAdapter,
    ) -> AnalysisReport:
        raise NotImplementedError


class Broken:
    pass


def test_bias_protocol_isinstance_positive():
    assert isinstance(GoodBias(), BiasAnalyzer)


def test_bias_protocol_isinstance_negative():
    assert not isinstance(Broken(), BiasAnalyzer)


def test_explain_protocol_isinstance_positive():
    assert isinstance(GoodExplain(), ExplainabilityAnalyzer)


def test_explain_protocol_isinstance_negative():
    assert not isinstance(Broken(), ExplainabilityAnalyzer)
