"""Fetch UCI Adult Census dataset and materialise as parquet.

Public dataset — https://archive.ics.uci.edu/ml/machine-learning-databases/adult/adult.data
Run: ``uv run python tests/fixtures/build_adult.py``.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pandas as pd

SOURCE = "https://archive.ics.uci.edu/ml/machine-learning-databases/adult/adult.data"
COLUMNS = [
    "age",
    "workclass",
    "fnlwgt",
    "education",
    "education_num",
    "marital_status",
    "occupation",
    "relationship",
    "race",
    "sex",
    "capital_gain",
    "capital_loss",
    "hours_per_week",
    "native_country",
    "income",
]
KEEP = [
    "age",
    "workclass",
    "education",
    "marital_status",
    "occupation",
    "relationship",
    "race",
    "sex",
    "hours_per_week",
    "native_country",
    "income",
]


def build() -> Path:
    """Download, clean, and write the Adult parquet.

    Returns:
        Path to the written parquet file.
    """
    here = Path(__file__).parent
    raw = here / "adult.data"
    if not raw.exists():
        urllib.request.urlretrieve(SOURCE, raw)
    df = pd.read_csv(raw, header=None, names=COLUMNS, skipinitialspace=True, na_values=["?"])
    df = df.dropna().reset_index(drop=True)[KEEP]
    df["income"] = df["income"].str.rstrip(".")
    out = here / "adult.parquet"
    df.to_parquet(out, index=False)
    return out


if __name__ == "__main__":
    path = build()
    print(f"wrote {path}")
