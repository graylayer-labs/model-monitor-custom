"""Generate deterministic test data for AWS E2E testing.

Produces small (100 rows, ~1MB) Parquet files for fast testing.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def generate_training_data() -> pd.DataFrame:
    """Generate training dataset for baseline analysis.

    Returns:
        DataFrame with features and target
    """
    np.random.seed(42)  # Deterministic
    n_rows = 100

    df = pd.DataFrame({
        "feature_numeric_1": np.random.normal(0, 1, n_rows),
        "feature_numeric_2": np.random.normal(100, 20, n_rows),
        "feature_categorical_1": np.random.choice(["A", "B", "C"], n_rows),
        "feature_categorical_2": np.random.choice(["X", "Y"], n_rows),
        "target": np.random.choice([0, 1], n_rows),
    })

    return df


def generate_predictions_data() -> pd.DataFrame:
    """Generate prediction dataset for live monitoring.

    Returns:
        DataFrame with features, predictions, and timestamps
    """
    np.random.seed(42)
    n_rows = 100

    # Slightly different distribution to trigger drift detection
    df = pd.DataFrame({
        "feature_numeric_1": np.random.normal(0.2, 1.1, n_rows),  # Shifted mean
        "feature_numeric_2": np.random.normal(105, 22, n_rows),  # Shifted mean
        "feature_categorical_1": np.random.choice(["A", "B", "C", "D"], n_rows),  # New category
        "feature_categorical_2": np.random.choice(["X", "Y"], n_rows),
        "prediction": np.random.choice([0, 1], n_rows),
        "prediction_score": np.random.uniform(0, 1, n_rows),
        "timestamp": pd.date_range("2026-08-01", periods=n_rows, freq="1h"),
    })

    return df


def save_test_data(output_dir: Path | str) -> dict[str, str]:
    """Save test data to Parquet files.

    Args:
        output_dir: Directory to save files to

    Returns:
        Dict with paths to saved files
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    training_path = output_dir / "training.parquet"
    predictions_path = output_dir / "predictions.parquet"

    generate_training_data().to_parquet(training_path, index=False)
    generate_predictions_data().to_parquet(predictions_path, index=False)

    return {
        "training": str(training_path),
        "predictions": str(predictions_path),
    }


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        paths = save_test_data(tmpdir)
        print(f"Training data: {paths['training']}")
        print(f"Predictions data: {paths['predictions']}")

        # Verify
        df_train = pd.read_parquet(paths["training"])
        df_pred = pd.read_parquet(paths["predictions"])
        print(f"\nTraining shape: {df_train.shape}")
        print(f"Predictions shape: {df_pred.shape}")
