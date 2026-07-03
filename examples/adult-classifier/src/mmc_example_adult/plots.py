"""Matplotlib renderings of the five analyser outputs.

Each helper takes a single :class:`~mmc_base.contract.AnalyserOutput` (plus
any structured extras it needs), writes a PNG under ``out_dir``, and
returns the resulting path so the caller can embed it into
``docs/e2e-output.md``.

Kept intentionally simple — matplotlib's non-interactive Agg backend, no
seaborn, no styling frameworks. The point is to prove each analyser
produced something plottable, not to win a design award.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

_TOP_FEATURE_COUNT = 10


def explain_shap_bar(payload: dict[str, Any], out_dir: Path) -> Path:
    """Render the top-K SHAP importances as a horizontal bar chart.

    Args:
        payload: :attr:`AnalyserOutput.payload` from :class:`ExplainAnalyser`.
        out_dir: Directory to write into. Created if missing.

    Returns:
        Path to the written PNG.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    values: dict[str, float] = payload["explanations"]["kernel_shap"]["values"]
    ranked = sorted(values.items(), key=lambda kv: abs(kv[1]), reverse=True)[:_TOP_FEATURE_COUNT]
    names = [n for n, _ in ranked][::-1]
    scores = [v for _, v in ranked][::-1]

    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.barh(names, scores, color="#4C72B0")
    ax.set_xlabel("mean |SHAP|")
    ax.set_title("Explain — top feature importances")
    fig.tight_layout()
    out = out_dir / "explain_shap_top_features.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def mq_confusion(payload: dict[str, Any], out_dir: Path) -> Path:
    """Render the MQ confusion matrix as a heatmap.

    Args:
        payload: :attr:`AnalyserOutput.payload` from :class:`MqAnalyser`.
        out_dir: Directory to write into.

    Returns:
        Path to the written PNG.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cm = np.asarray(payload["confusion_matrix"]["rows"], dtype=int)
    labels = payload["confusion_matrix"]["labels"]

    fig, ax = plt.subplots(figsize=(4.2, 3.6))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(labels)), labels=labels)
    ax.set_yticks(range(len(labels)), labels=labels)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title("MQ — confusion matrix")
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(j, i, str(cm[i, j]), ha="center", va="center", color="black")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    out = out_dir / "mq_confusion_matrix.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def dq_drift_heatmap(payload: dict[str, Any], out_dir: Path) -> Path:
    """Render per-column drift statistics as a heatmap.

    Args:
        payload: :attr:`AnalyserOutput.payload` from :class:`DqAnalyser`.
        out_dir: Directory to write into.

    Returns:
        Path to the written PNG.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    drift: dict[str, dict[str, float]] = payload["drift"]
    columns = list(drift.keys())
    stats = sorted({stat for col in drift.values() for stat in col})
    matrix = np.array([[drift[col].get(stat, np.nan) for stat in stats] for col in columns], dtype=float)

    fig, ax = plt.subplots(figsize=(5.2, max(2.0, 0.35 * len(columns) + 1.5)))
    im = ax.imshow(matrix, aspect="auto", cmap="magma")
    ax.set_yticks(range(len(columns)), labels=columns)
    ax.set_xticks(range(len(stats)), labels=stats)
    ax.set_title("DQ — drift stats per column")
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            value = matrix[i, j]
            if not np.isnan(value):
                ax.text(j, i, f"{value:.2f}", ha="center", va="center", color="white", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    out = out_dir / "dq_drift_heatmap.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def shadow_agreement_histogram(payload: dict[str, Any], out_dir: Path) -> Path:
    """Render serving-vs-shadow per-class disagreement rates as a bar chart.

    Args:
        payload: :attr:`AnalyserOutput.payload` from :class:`ShadowAnalyser`.
        out_dir: Directory to write into.

    Returns:
        Path to the written PNG.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    per_class: dict[str, float] = payload["disagreement_per_class"]
    classes = list(per_class.keys())
    rates = [per_class[c] for c in classes]

    fig, ax = plt.subplots(figsize=(5.0, 3.0))
    ax.bar(classes, rates, color="#DD8452")
    ax.axhline(1 - payload["agreement_rate"], color="#333333", linestyle="--", label="overall disagreement")
    ax.set_ylabel("disagreement rate")
    ax.set_title("Shadow — per-class disagreement")
    ax.legend()
    fig.tight_layout()
    out = out_dir / "shadow_per_class_disagreement.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def bias_dpl_bar(payload: dict[str, Any], out_dir: Path) -> Path:
    """Render pre-training bias metrics for each facet as grouped bars.

    Args:
        payload: :attr:`AnalyserOutput.payload` from :class:`BiasAnalyser`.
        out_dir: Directory to write into.

    Returns:
        Path to the written PNG.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    facets: dict[str, list[dict[str, Any]]] = payload["pre_training_bias"]["facets"]
    metric_names: list[str] = []
    for entries in facets.values():
        for entry in entries:
            if entry["name"] not in metric_names:
                metric_names.append(entry["name"])

    fig, ax = plt.subplots(figsize=(6.0, 3.2))
    facet_names = list(facets.keys())
    width = 0.8 / max(len(facet_names), 1)
    for i, facet_name in enumerate(facet_names):
        by_metric = {e["name"]: float(e["value"]) for e in facets[facet_name]}
        values = [by_metric.get(metric_name, 0.0) for metric_name in metric_names]
        positions = np.arange(len(metric_names)) + i * width
        ax.bar(positions, values, width=width, label=facet_name)
    ax.set_xticks(np.arange(len(metric_names)) + width * (len(facet_names) - 1) / 2, labels=metric_names)
    ax.set_ylabel("value")
    ax.set_title("Bias — pre-training metrics")
    ax.legend()
    fig.tight_layout()
    out = out_dir / "bias_pre_training.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out
