# Test fixtures

Small, committed datasets that keep the RED tests reproducible offline.

## `adult.parquet`

UCI Adult Census dataset. Public, standard bias-tutorial dataset. Rows with any missing value are dropped. Columns kept: `age, workclass, education, marital_status, occupation, relationship, race, sex, hours_per_week, native_country, income`. `income` is the label (`>50K` / `<=50K`).

Regenerate: `uv run python tests/fixtures/build_adult.py`. The raw `adult.data` source is gitignored — the script re-fetches it.

## `synthetic_3class.parquet`

Deterministic 3-class classification data for Kernel SHAP tests. Built from `sklearn.datasets.make_classification(n_samples=500, n_features=10, n_classes=3, n_informative=5, random_state=42)` — same seed every time.

Regenerate: `uv run python tests/fixtures/build_synthetic.py`.
