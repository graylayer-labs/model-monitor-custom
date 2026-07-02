"""RED contract tests for `ModelAdapter` ABC."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest
from model_baseline.adapters.base import ModelAdapter

if TYPE_CHECKING:
    pass


def test_abstract_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        ModelAdapter()  # type: ignore[abstract]


def test_subclass_missing_method_raises():
    class Partial(ModelAdapter):
        def load(self, model_uri: str) -> None:
            return None

    with pytest.raises(TypeError):
        Partial()  # type: ignore[abstract]


def test_full_subclass_instantiates():
    class Complete(ModelAdapter):
        def load(self, model_uri: str) -> None:
            return None

        def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
            return np.zeros((len(features), 2))

        def feature_headers(self) -> list[str]:
            return ["a", "b"]

        def class_labels(self) -> list[str]:
            return ["neg", "pos"]

    adapter = Complete()
    assert isinstance(adapter, ModelAdapter)
