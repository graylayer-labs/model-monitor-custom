from __future__ import annotations

import pickle  # noqa: S403 — trusted local artefacts in test
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb
from analyser_explain.adapters import SklearnAdapter, XGBoostAdapter, load_adapter
from sklearn.datasets import make_classification
from sklearn.linear_model import LogisticRegression


@pytest.fixture
def sklearn_model(tmp_path: Path) -> tuple[Path, list[str], list[str]]:
    x, y = make_classification(n_samples=100, n_features=4, n_classes=2, random_state=0)
    headers = [f"f{i}" for i in range(4)]
    frame = pd.DataFrame(x, columns=headers)
    model = LogisticRegression(max_iter=200).fit(frame, y)
    path = tmp_path / "model.pkl"
    with path.open("wb") as fh:
        pickle.dump(model, fh)
    return path, headers, [str(c) for c in model.classes_]


@pytest.fixture
def xgb_model(tmp_path: Path) -> tuple[Path, list[str]]:
    x, y = make_classification(n_samples=200, n_features=6, n_classes=3, n_informative=4, random_state=0)
    headers = [f"f{i}" for i in range(6)]
    dmat = xgb.DMatrix(x, label=y, feature_names=headers)
    booster = xgb.train({"objective": "multi:softprob", "num_class": 3, "max_depth": 3}, dmat, num_boost_round=5)
    path = tmp_path / "model.json"
    booster.save_model(str(path))
    return path, headers


def test_sklearn_adapter_loads_and_predicts(sklearn_model):
    path, headers, labels = sklearn_model
    adapter = SklearnAdapter()
    adapter.load(str(path))
    assert adapter.feature_headers() == headers
    assert adapter.class_labels() == labels
    frame = pd.DataFrame(np.zeros((3, 4)), columns=headers)
    proba = adapter.predict_proba(frame)
    assert proba.shape == (3, 2)
    assert np.allclose(proba.sum(axis=1), 1.0)


def test_sklearn_adapter_load_via_factory(sklearn_model):
    path, headers, _ = sklearn_model
    adapter = load_adapter(str(path), "sklearn")
    assert isinstance(adapter, SklearnAdapter)
    assert adapter.feature_headers() == headers


def test_xgb_adapter_loads_and_predicts(xgb_model):
    path, headers = xgb_model
    adapter = XGBoostAdapter()
    adapter.load(str(path))
    assert adapter.feature_headers() == headers
    frame = pd.DataFrame(np.zeros((4, 6)), columns=headers)
    proba = adapter.predict_proba(frame)
    assert proba.shape == (4, 3)
    assert np.allclose(proba.sum(axis=1), 1.0, atol=1e-5)


def test_xgb_adapter_load_via_factory(xgb_model):
    path, _ = xgb_model
    adapter = load_adapter(str(path), "xgboost")
    assert isinstance(adapter, XGBoostAdapter)


def test_load_adapter_rejects_unknown_framework(tmp_path: Path):
    with pytest.raises(ValueError, match="Unknown framework"):
        load_adapter(str(tmp_path / "x"), "tensorflow")  # ty: ignore[invalid-argument-type]


def test_sklearn_adapter_predict_before_load_raises():
    adapter = SklearnAdapter()
    with pytest.raises(RuntimeError, match="load"):
        adapter.predict_proba(pd.DataFrame({"f0": [0.0]}))


def test_xgb_adapter_predict_before_load_raises():
    adapter = XGBoostAdapter()
    with pytest.raises(RuntimeError, match="load"):
        adapter.predict_proba(pd.DataFrame({"f0": [0.0]}))
