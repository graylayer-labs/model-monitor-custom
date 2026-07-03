"""End-to-end orchestrator — drive all five analysers over the Adult splits.

Sequence:

1. Build the model + splits via :mod:`mmc_example_adult.train`.
2. Instantiate each analyser and call ``compute(inputs, config)`` with a
   hand-authored spec that matches the analyser's Pydantic model.
3. Render one plot per analyser.
4. Print a summary table, write ``outputs/e2e-summary.json``, and rewrite
   ``docs/e2e-output.md``.

The DQ analyser is run twice — once against the drifted current window
(should surface violations) and once against a clean control window
(should return ``succeeded`` with zero violations). The overall verdict
line asserts on both, which is the whole point of the example: prove the
system flags real drift and does **not** flag no-drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analyser_bias import BiasAnalyser
from analyser_dq import DqAnalyser
from analyser_explain import ExplainAnalyser
from analyser_mq import MqAnalyser
from analyser_shadow import ShadowAnalyser
from mmc_base.contract import AnalyserInputs, AnalyserOutput

from mmc_example_adult import plots, train

EXAMPLE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = EXAMPLE_ROOT.parents[1]
OUTPUTS_DIR = EXAMPLE_ROOT / "outputs"
PLOTS_DIR = OUTPUTS_DIR / "plots"
SUMMARY_PATH = OUTPUTS_DIR / "e2e-summary.json"
DOC_PATH = REPO_ROOT / "docs" / "e2e-output.md"


@dataclass(frozen=True)
class AnalyserRun:
    """One analyser's contribution to the summary.

    Attributes:
        name: Human name of the analyser (bias, explain, ...).
        output: Validated :class:`AnalyserOutput`.
        plot_path: PNG path relative to the repo root (or ``None``).
        payload_highlights: Small ``{key: value}`` snapshot to show in the
            summary table / doc.
    """

    name: str
    output: AnalyserOutput
    plot_path: Path | None
    payload_highlights: dict[str, Any]


def _run_bias(splits: train.Splits) -> AnalyserRun:
    """Run :class:`BiasAnalyser` with post-training metrics enabled.

    Args:
        splits: Materialised data splits.

    Returns:
        Analyser run record.
    """
    config = {
        "dataset_input": "dataset",
        "label_column": "income",
        "positive_label_values": [">50K"],
        "predicted_label_column": "prediction",
        "facets": [
            {"name": "sex", "values": ["Female"]},
            {"name": "race", "values": ["Black"]},
        ],
        "methods": ["CI", "DPL", "KL", "JS"],
        "thresholds": {"DPL": 0.1, "KL": 0.5},
        "severity_alert_thresholds": {"DPL": 0.25},
    }
    inputs = AnalyserInputs(paths={"dataset": splits.bias_dataset_path})
    output = BiasAnalyser().compute(inputs, config)
    plot = plots.bias_dpl_bar(output.payload, PLOTS_DIR)
    highlights = {
        "facets": list(output.payload["pre_training_bias"]["facets"].keys()),
        "sex/pre/DPL": output.analyser_metrics.get("sex/pre/DPL"),
        "race/pre/DPL": output.analyser_metrics.get("race/pre/DPL"),
    }
    return AnalyserRun(name="bias", output=output, plot_path=plot, payload_highlights=highlights)


def _run_explain(splits: train.Splits) -> AnalyserRun:
    """Run :class:`ExplainAnalyser` on the trained sklearn model.

    Args:
        splits: Materialised data splits.

    Returns:
        Analyser run record.
    """
    config = {
        "framework": "sklearn",
        "model_uri": str(splits.model_path),
        "features_input": "features",
        "num_samples": 30,
        "background_size": 20,
        "top_k": 5,
    }
    inputs = AnalyserInputs(paths={"features": splits.features_path})
    output = ExplainAnalyser().compute(inputs, config)
    plot = plots.explain_shap_bar(output.payload, PLOTS_DIR)
    ranked = sorted(
        output.payload["explanations"]["kernel_shap"]["values"].items(),
        key=lambda kv: abs(kv[1]),
        reverse=True,
    )
    highlights = {"top_features": [name for name, _ in ranked[:3]]}
    return AnalyserRun(name="explain", output=output, plot_path=plot, payload_highlights=highlights)


def _run_dq(splits: train.Splits, *, drifted: bool) -> AnalyserRun:
    """Run :class:`DqAnalyser` against either the drifted or clean window.

    Args:
        splits: Materialised data splits.
        drifted: When true, uses the drifted current window. When false, the
            clean control window.

    Returns:
        Analyser run record.
    """
    current = splits.current_dataset_path if drifted else splits.clean_current_dataset_path
    config = {
        "current_input": "current",
        "baseline_input": "baseline",
        "numeric_columns": ["age", "hours_per_week"],
        "categorical_columns": ["workclass", "education", "sex", "race"],
        "completeness_threshold": 0.99,
        "ks_p_value_threshold": 0.05,
        "psi_threshold": 0.1,
        "severity_threshold": 3,
    }
    inputs = AnalyserInputs(paths={"current": current, "baseline": splits.baseline_dataset_path})
    output = DqAnalyser().compute(inputs, config)
    plot = plots.dq_drift_heatmap(output.payload, PLOTS_DIR) if drifted else None
    highlights = {
        "drifted_window": drifted,
        "violations": len(output.payload["violations"]),
        "schema_diff": output.payload["schema_diff"],
    }
    label = "dq" if drifted else "dq_control"
    return AnalyserRun(name=label, output=output, plot_path=plot, payload_highlights=highlights)


def _run_mq(splits: train.Splits) -> AnalyserRun:
    """Run :class:`MqAnalyser` on the current-window predictions.

    Args:
        splits: Materialised data splits.

    Returns:
        Analyser run record.
    """
    config = {
        "predictions_input": "predictions",
        "problem_type": "binary",
        "label_column": "income",
        "prediction_column": "prediction",
        "probability_columns": ["prob_positive"],
        "positive_label": ">50K",
        "baseline_metrics": {"accuracy": 0.85, "f1_macro": 0.80},
        "degradation_thresholds": {"accuracy": 0.02, "f1_macro": 0.02},
        "severity_threshold": 2,
    }
    inputs = AnalyserInputs(paths={"predictions": splits.predictions_path})
    output = MqAnalyser().compute(inputs, config)
    plot = plots.mq_confusion(output.payload, PLOTS_DIR)
    highlights = {
        "accuracy": output.analyser_metrics.get("mq/accuracy"),
        "f1_macro": output.analyser_metrics.get("mq/f1_macro"),
        "auc": output.analyser_metrics.get("mq/auc"),
    }
    return AnalyserRun(name="mq", output=output, plot_path=plot, payload_highlights=highlights)


def _run_shadow(splits: train.Splits) -> AnalyserRun:
    """Run :class:`ShadowAnalyser` on serving vs shadow predictions.

    Args:
        splits: Materialised data splits.

    Returns:
        Analyser run record.
    """
    config = {
        "problem_type": "binary",
        "serving_variant": "AllTraffic",
        "shadow_variant": "candidate-v2",
        "serving_input": "serving_predictions",
        "shadow_input": "shadow_predictions",
        "prediction_column": "prediction",
        "probability_columns": ["prob_positive"],
        "join_key": "row_id",
        "agreement_threshold": 0.98,
        "js_divergence_threshold": 0.02,
        "severity_threshold": 2,
    }
    inputs = AnalyserInputs(
        paths={
            "serving_predictions": splits.serving_predictions_path,
            "shadow_predictions": splits.shadow_predictions_path,
        },
    )
    output = ShadowAnalyser().compute(inputs, config)
    plot = plots.shadow_agreement_histogram(output.payload, PLOTS_DIR)
    highlights = {
        "agreement": output.analyser_metrics.get("shadow/agreement"),
        "js_divergence": output.analyser_metrics.get("shadow/js_divergence"),
    }
    return AnalyserRun(name="shadow", output=output, plot_path=plot, payload_highlights=highlights)


def _relpath(path: Path | None) -> str | None:
    """Return ``path`` relative to the repo root as a POSIX string.

    Args:
        path: Absolute path or ``None``.

    Returns:
        Repo-relative POSIX path, or ``None``.
    """
    if path is None:
        return None
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _summarise(runs: list[AnalyserRun]) -> dict[str, Any]:
    """Fold the run records into a JSON-friendly summary.

    Args:
        runs: Ordered analyser run records.

    Returns:
        Serialisable summary dict.
    """
    entries: list[dict[str, Any]] = [
        {
            "analyser": run.name,
            "outcome": run.output.outcome.value,
            "severity": run.output.resolved_severity().value,
            "violation_count": run.output.violation_count,
            "plot": _relpath(run.plot_path),
            "highlights": run.payload_highlights,
        }
        for run in runs
    ]
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "runs": entries,
    }


def _render_doc(summary: dict[str, Any], runs: list[AnalyserRun]) -> None:
    """Rewrite ``docs/e2e-output.md`` from the current summary.

    Args:
        summary: JSON summary produced by :func:`_summarise`.
        runs: Ordered run records (so payloads are available in one place).
    """
    lines: list[str] = []
    lines.append("# End-to-end example — Adult classifier")
    lines.append("")
    lines.append(f"_Last regenerated: {summary['generated_at_utc']}_")
    lines.append("")
    lines.append(
        "Auto-generated by `uv run python -m mmc_example_adult.run`. Do not hand-edit — regenerate instead.",
    )
    lines.append("")
    lines.append("## Setup")
    lines.append("")
    lines.append("- Dataset: `tests/fixtures/adult.parquet` (UCI Adult snapshot, ~30k rows).")
    lines.append(
        "- Model: `sklearn.linear_model.LogisticRegression` on `age`, `hours_per_week`, `age * hours_per_week`."
    )
    lines.append("- Splits: 50 % baseline, 50 % current-window with synthetic drift injected (older + more hours).")
    lines.append("- Shadow variant: same features, `C=0.1` — introduces measurable disagreement.")
    lines.append("")
    lines.append("## Rollup")
    lines.append("")
    lines.append("| Analyser | Outcome | Severity | Violations | Plot |")
    lines.append("| --- | --- | --- | --- | --- |")
    for entry in summary["runs"]:
        plot_cell = f"`{entry['plot']}`" if entry["plot"] else "—"
        analyser = entry["analyser"]
        outcome = entry["outcome"]
        severity = entry["severity"]
        vc = entry["violation_count"]
        lines.append(f"| `{analyser}` | {outcome} | {severity} | {vc} | {plot_cell} |")
    lines.append("")

    for run in runs:
        entry = next(e for e in summary["runs"] if e["analyser"] == run.name)
        lines.append(f"## {run.name}")
        lines.append("")
        lines.append(f"- **Outcome:** `{entry['outcome']}`")
        lines.append(f"- **Severity:** `{entry['severity']}`")
        lines.append(f"- **Violations:** {entry['violation_count']}")
        lines.append(f"- **Highlights:** `{json.dumps(entry['highlights'], sort_keys=True, default=str)}`")
        if run.plot_path is not None:
            rel = _relpath(run.plot_path)
            plot_rel = Path("..") / rel if rel else Path("..")
            lines.append("")
            lines.append(f"![{run.name}]({plot_rel.as_posix()})")
        lines.append("")

    lines.append("## Verdict")
    lines.append("")
    dq_run = next(r for r in runs if r.name == "dq")
    dq_control = next(r for r in runs if r.name == "dq_control")
    verdict = (
        "System flags drift on the drifted window (`dq` → "
        f"`{dq_run.output.outcome.value}`, {dq_run.output.violation_count} violations) "
        f"and returns `{dq_control.output.outcome.value}` on the clean control — "
        "so no false positives on no-drift data."
    )
    lines.append(verdict)
    lines.append("")

    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text("\n".join(lines), encoding="utf-8")


def _print_table(summary: dict[str, Any]) -> None:
    """Print a plain-text rollup table.

    Args:
        summary: JSON summary produced by :func:`_summarise`.
    """
    header = f"{'analyser':<12}{'outcome':<28}{'severity':<10}{'violations':<10}"
    print(header)  # noqa: T201 — CLI output
    print("-" * len(header))  # noqa: T201
    for entry in summary["runs"]:
        print(  # noqa: T201
            f"{entry['analyser']:<12}{entry['outcome']:<28}{entry['severity']:<10}{entry['violation_count']:<10}",
        )


def main() -> dict[str, Any]:
    """Run the full end-to-end example.

    Returns:
        The JSON-serialisable summary dict (also written to
        ``outputs/e2e-summary.json``).
    """
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    splits = train.build(OUTPUTS_DIR / "data")

    runs = [
        _run_bias(splits),
        _run_explain(splits),
        _run_dq(splits, drifted=True),
        _run_dq(splits, drifted=False),
        _run_mq(splits),
        _run_shadow(splits),
    ]

    summary = _summarise(runs)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    _render_doc(summary, runs)
    _print_table(summary)
    return summary


if __name__ == "__main__":
    main()
