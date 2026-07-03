# Adult classifier — end-to-end example

Trains a small `LogisticRegression` on the UCI Adult fixture (`tests/fixtures/adult.parquet`),
slices it into current / baseline / shadow windows, then exercises every one of the five real
analysers locally against those slices — no AWS, no containers, no SageMaker.

The run prints a summary table, writes `outputs/e2e-summary.json`, saves plots under
`outputs/plots/`, and regenerates `docs/e2e-output.md` for the README hero shot.

## Run

```
uv run python -m mmc_example_adult.run
```

Outputs (relative to repo root):

- `examples/adult-classifier/outputs/e2e-summary.json` — machine-readable rollup.
- `examples/adult-classifier/outputs/plots/*.png` — one PNG per analyser.
- `docs/e2e-output.md` — human-readable snapshot with tables + embedded plots.

## What it proves

- Every analyser (`bias`, `explain`, `dq`, `mq`, `shadow`) accepts real hand-authored config
  and returns a validated `AnalyserOutput`.
- Baseline vs current split produces measurable drift; shadow variant produces measurable
  disagreement — both surface as `succeeded_with_violations`.
- The zero-drift control slice returns `succeeded` with `violation_count == 0`.
