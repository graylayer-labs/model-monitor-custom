"""Generate a deterministic 3-class classification parquet for SHAP tests.

Run: ``uv run python tests/fixtures/build_synthetic.py``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sklearn.datasets import make_classification


def build() -> Path:
    """Write ``synthetic_3class.parquet`` next to this script.

    Returns:
        Path to the written parquet file.
    """
    x, y = make_classification(
        n_samples=500,
        n_features=10,
        n_classes=3,
        n_informative=5,
        random_state=42,
    )
    df = pd.DataFrame(x, columns=[f"f{i}" for i in range(x.shape[1])])
    df["label"] = y
    out = Path(__file__).parent / "synthetic_3class.parquet"
    df.to_parquet(out, index=False)
    return out


if __name__ == "__main__":
    path = build()
    print(f"wrote {path}")
