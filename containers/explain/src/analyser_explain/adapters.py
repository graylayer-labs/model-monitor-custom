"""Thin model adapters used by :class:`ExplainAnalyser`.

Concrete implementations of Phase 1's :class:`~model_baseline.adapters.base.ModelAdapter`
ABC. Load a pickled sklearn estimator or an XGBoost booster from a local path (the base
image has already fetched the artefact from S3) and expose ``predict_proba``,
``feature_headers``, and ``class_labels``.

No AWS SDK calls. No ``sagemaker`` imports.
"""

from __future__ import annotations

import pickle  # noqa: S403 — model artefacts are trusted, already fetched by base
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np
import xgboost as xgb
from model_baseline.adapters.base import ModelAdapter

if TYPE_CHECKING:
    import pandas as pd

Framework = Literal["sklearn", "xgboost"]


class SklearnAdapter(ModelAdapter):
    """Adapter over a pickled sklearn estimator exposing ``predict_proba``."""

    def __init__(self) -> None:
        """Create an empty adapter — call :meth:`load` before use."""
        self._model: object | None = None
        self._headers: list[str] = []
        self._labels: list[str] = []

    def load(self, model_uri: str) -> None:
        """Unpickle the estimator from a local path.

        Args:
            model_uri: Local filesystem path to a pickled sklearn estimator.
        """
        with Path(model_uri).open("rb") as fh:
            model = pickle.load(fh)  # noqa: S301 — trusted local artefact
        self._model = model
        feature_attr = getattr(model, "feature_names_in_", None)
        self._headers = list(feature_attr) if feature_attr is not None else []
        classes = getattr(model, "classes_", None)
        self._labels = [str(c) for c in classes] if classes is not None else []

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        """Return class probabilities.

        Args:
            features: Feature dataframe matching :meth:`feature_headers`.

        Returns:
            Array of shape ``[n_rows, n_classes]``.

        Raises:
            RuntimeError: If :meth:`load` has not been called yet.
        """
        if self._model is None:
            msg = "SklearnAdapter.load() must be called before predict_proba()"
            raise RuntimeError(msg)
        return np.asarray(self._model.predict_proba(features))  # ty: ignore[unresolved-attribute]

    def feature_headers(self) -> list[str]:
        """Return ordered feature column names."""
        return list(self._headers)

    def class_labels(self) -> list[str]:
        """Return ordered class labels."""
        return list(self._labels)

    def set_metadata(self, headers: list[str], labels: list[str]) -> None:
        """Set feature headers and class labels explicitly.

        Useful when the sklearn model was fitted on a numpy array and so lacks
        ``feature_names_in_``.

        Args:
            headers: Ordered feature column names.
            labels: Ordered class labels.
        """
        self._headers = list(headers)
        self._labels = list(labels)


class XGBoostAdapter(ModelAdapter):
    """Adapter over an XGBoost booster serialised as ``.json`` or ``.ubj``."""

    def __init__(self) -> None:
        """Create an empty adapter — call :meth:`load` before use."""
        self._booster: xgb.Booster | None = None
        self._headers: list[str] = []
        self._labels: list[str] = []

    def load(self, model_uri: str) -> None:
        """Load the booster from a local path.

        Args:
            model_uri: Local filesystem path to an XGBoost ``.json`` / ``.ubj`` model.
        """
        booster = xgb.Booster()
        booster.load_model(model_uri)
        self._booster = booster
        feature_names = booster.feature_names or []
        self._headers = list(feature_names)

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        """Return class probabilities from the booster.

        Args:
            features: Feature dataframe matching :meth:`feature_headers`.

        Returns:
            Array of shape ``[n_rows, n_classes]``. Binary boosters return a
            2-column ``[1-p, p]`` matrix so the shape is uniform across frameworks.

        Raises:
            RuntimeError: If :meth:`load` has not been called yet.
        """
        if self._booster is None:
            msg = "XGBoostAdapter.load() must be called before predict_proba()"
            raise RuntimeError(msg)
        dmat = xgb.DMatrix(features, feature_names=self._headers or None)
        raw = np.asarray(self._booster.predict(dmat))
        if raw.ndim == 1:
            return np.column_stack([1.0 - raw, raw])
        return raw

    def feature_headers(self) -> list[str]:
        """Return ordered feature column names."""
        return list(self._headers)

    def class_labels(self) -> list[str]:
        """Return ordered class labels."""
        return list(self._labels)

    def set_metadata(self, headers: list[str], labels: list[str]) -> None:
        """Set feature headers and class labels explicitly.

        Args:
            headers: Ordered feature column names.
            labels: Ordered class labels.
        """
        self._headers = list(headers)
        self._labels = list(labels)


def load_adapter(model_uri: str, framework: Framework) -> ModelAdapter:
    """Return an initialised :class:`ModelAdapter` for the given framework.

    Args:
        model_uri: Local filesystem path to the model artefact.
        framework: Which adapter to construct — ``"sklearn"`` or ``"xgboost"``.

    Returns:
        A loaded adapter ready for ``predict_proba``.

    Raises:
        ValueError: If ``framework`` is not one of the supported values.
    """
    adapter: ModelAdapter
    if framework == "sklearn":
        adapter = SklearnAdapter()
    elif framework == "xgboost":
        adapter = XGBoostAdapter()
    else:
        msg = f"Unknown framework {framework!r} — expected 'sklearn' or 'xgboost'"
        raise ValueError(msg)
    adapter.load(model_uri)
    return adapter
