"""Train a small Adult income classifier and materialise data splits.

The example works from the checked-in ``tests/fixtures/adult.parquet`` — a
cleaned UCI Adult snapshot with mixed numeric + categorical columns. This
module turns that snapshot into everything the five analysers need:

- A fitted ``LogisticRegression`` (pickled) plus the numeric feature frame
  used to fit it — consumed by :class:`ExplainAnalyser`.
- A **baseline** slice + a **current** slice that has a synthetic drift
  injected (older population + shifted hours-per-week) — consumed by
  :class:`DqAnalyser`.
- A **predictions** parquet holding ground-truth + hard-label prediction +
  positive-class probability — consumed by :class:`MqAnalyser`.
- A **serving** vs **shadow** predictions pair — the shadow variant is a
  slightly-differently-configured LR whose disagreement with serving is
  measurable — consumed by :class:`ShadowAnalyser`.

Zero AWS SDK calls; every path returned is on the local filesystem.
"""

from __future__ import annotations

import pickle  # ruff: ignore[suspicious-pickle-import] — model artefact is local + trusted
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

FIXTURE_PATH = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "adult.parquet"

NUMERIC_FEATURES: tuple[str, ...] = ("age", "hours_per_week")
DERIVED_FEATURE = "age_x_hours"
LABEL_COLUMN = "income"
POSITIVE_LABEL = ">50K"


@dataclass(frozen=True)
class Splits:
    """Materialised artefacts for one end-to-end run.

    Attributes:
        model_path: Pickled sklearn estimator (features + label already
            projected).
        features_path: Parquet of the numeric feature frame used to fit the
            model — passed straight to :class:`ExplainAnalyser`.
        baseline_dataset_path: Adult snapshot as-is (baseline).
        current_dataset_path: Adult snapshot with drift injected.
        clean_current_dataset_path: Adult snapshot with **no** drift — used
            as a control to prove the DQ analyser flags real drift only.
        predictions_path: Current-window predictions (label + prediction +
            probability).
        serving_predictions_path: Serving variant's hard-labels + probs.
        shadow_predictions_path: Shadow variant's hard-labels + probs (row-
            aligned with serving_predictions_path via ``row_id``).
        bias_dataset_path: Snapshot with prediction column added — used by
            :class:`BiasAnalyser` for post-training bias metrics too.
    """

    model_path: Path
    features_path: Path
    baseline_dataset_path: Path
    current_dataset_path: Path
    clean_current_dataset_path: Path
    predictions_path: Path
    serving_predictions_path: Path
    shadow_predictions_path: Path
    bias_dataset_path: Path


def _load_adult() -> pd.DataFrame:
    """Return the Adult fixture as a fresh dataframe.

    Returns:
        The cleaned UCI Adult snapshot.

    Raises:
        FileNotFoundError: When the fixture is missing.
    """
    if not FIXTURE_PATH.exists():
        msg = f"Adult fixture missing at {FIXTURE_PATH}. Rebuild with tests/fixtures/build_adult.py."
        raise FileNotFoundError(msg)
    return pd.read_parquet(FIXTURE_PATH)


def _feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Project the frame down to the numeric feature columns used by the model.

    Args:
        df: Full Adult frame.

    Returns:
        A copy holding only the numeric features + derived interaction.
    """
    frame = pd.DataFrame(
        {
            "age": df["age"].astype(float),
            "hours_per_week": df["hours_per_week"].astype(float),
        },
    )
    frame[DERIVED_FEATURE] = frame["age"] * frame["hours_per_week"]
    return frame


def _inject_drift(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Return a drifted copy — older population, more hours worked.

    Args:
        df: Baseline frame.
        rng: Seeded random number generator.

    Returns:
        Drifted frame; row count is preserved.
    """
    drifted = df.copy()
    drifted["age"] = np.clip(drifted["age"] + rng.integers(3, 12, size=len(drifted)), 17, 90)
    drifted["hours_per_week"] = np.clip(drifted["hours_per_week"] + rng.integers(4, 10, size=len(drifted)), 1, 99)
    return drifted


def _predict(model: LogisticRegression, features: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(hard_labels, positive_probabilities)`` for ``features``.

    Args:
        model: Fitted estimator with ``predict`` + ``predict_proba``.
        features: Feature frame matching training columns.

    Returns:
        Tuple of hard predictions and probability-of-positive-class arrays.
    """
    hard = model.predict(features)
    probs = model.predict_proba(features)
    positive_idx = list(model.classes_).index(POSITIVE_LABEL)
    return hard, probs[:, positive_idx]


def build(out_dir: Path) -> Splits:  # ruff: ignore[too-many-locals] — one linear script that materialises 9 artefacts
    """Train the model and materialise every artefact the analysers need.

    Args:
        out_dir: Directory the artefacts land in. Created if missing.

    Returns:
        A :class:`Splits` record of every file path written.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed=0)

    adult = _load_adult()
    baseline = adult.sample(frac=0.5, random_state=0).reset_index(drop=True)
    current_clean = adult.drop(baseline.index, errors="ignore").reset_index(drop=True)
    current = _inject_drift(current_clean, rng)

    features_baseline = _feature_frame(baseline)
    labels_baseline = baseline[LABEL_COLUMN].to_numpy()

    model = LogisticRegression(max_iter=500, random_state=0).fit(features_baseline, labels_baseline)

    model_path = out_dir / "model.pkl"
    with model_path.open("wb") as fh:
        pickle.dump(model, fh)

    features_path = out_dir / "features.parquet"
    features_baseline.to_parquet(features_path, index=False)

    baseline_path = out_dir / "baseline.parquet"
    baseline.to_parquet(baseline_path, index=False)

    current_path = out_dir / "current.parquet"
    current.to_parquet(current_path, index=False)

    clean_current_path = out_dir / "current_clean.parquet"
    current_clean.to_parquet(clean_current_path, index=False)

    features_current = _feature_frame(current)
    hard, prob_pos = _predict(model, features_current)

    predictions = current.copy()
    predictions["prediction"] = hard
    predictions["prob_positive"] = prob_pos
    predictions_path = out_dir / "predictions.parquet"
    predictions.to_parquet(predictions_path, index=False)

    bias_dataset_path = out_dir / "bias_dataset.parquet"
    predictions.to_parquet(bias_dataset_path, index=False)

    serving = predictions[["prediction", "prob_positive"]].copy()
    serving["row_id"] = np.arange(len(serving))
    serving_path = out_dir / "serving_predictions.parquet"
    serving.to_parquet(serving_path, index=False)

    shadow_train_features = features_baseline[["age"]]
    shadow_current_features = features_current[["age"]]
    shadow_model = LogisticRegression(max_iter=500, random_state=1).fit(shadow_train_features, labels_baseline)
    shadow_hard, shadow_prob_pos = _predict(shadow_model, shadow_current_features)
    shadow = pd.DataFrame(
        {
            "prediction": shadow_hard,
            "prob_positive": shadow_prob_pos,
            "row_id": np.arange(len(shadow_hard)),
        },
    )
    shadow_path = out_dir / "shadow_predictions.parquet"
    shadow.to_parquet(shadow_path, index=False)

    return Splits(
        model_path=model_path,
        features_path=features_path,
        baseline_dataset_path=baseline_path,
        current_dataset_path=current_path,
        clean_current_dataset_path=clean_current_path,
        predictions_path=predictions_path,
        serving_predictions_path=serving_path,
        shadow_predictions_path=shadow_path,
        bias_dataset_path=bias_dataset_path,
    )
