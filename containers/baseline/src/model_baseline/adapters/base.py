"""Abstract base class for model adapters.

A `ModelAdapter` wraps a trained model artefact and exposes a uniform
prediction interface to the baseline analyzers. Concrete adapters are
introduced in Phase 2 (e.g. XGBoost, PyTorch).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd


class ModelAdapter(ABC):
    """Uniform prediction interface consumed by analyzers.

    Subclasses must implement all four abstract methods. The adapter owns
    model loading and prediction; it is deliberately framework-agnostic.
    """

    @abstractmethod
    def load(self, model_uri: str) -> None:
        """Pull the model artefact from S3 or a local path.

        Args:
            model_uri: `s3://bucket/key` or a local filesystem path.
        """

    @abstractmethod
    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        """Return class probabilities for each row of ``features``.

        Args:
            features: Feature dataframe with columns matching
                :meth:`feature_headers`.

        Returns:
            Array shaped ``[n, n_classes]``. Rows sum to 1 along axis 1.
        """

    @abstractmethod
    def feature_headers(self) -> list[str]:
        """Return the ordered feature column names the adapter expects."""

    @abstractmethod
    def class_labels(self) -> list[str]:
        """Return the ordered class labels matching :meth:`predict_proba` columns."""
