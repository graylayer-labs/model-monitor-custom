"""Real model-quality analyser.

Loads a parquet predictions frame (label + prediction + optional probability
columns), computes accuracy / precision / recall / F1 (macro) plus AUC for
binary problems when probabilities are supplied, and compares each metric
to a baseline snapshot. Metrics whose degradation exceeds their configured
threshold are logged as violations.

Zero AWS SDK calls — the base image owns all IO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pandas as pd
from mmc_base.contract import AnalyserOutput, Outcome, Severity
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from analyser_mq.spec import MqSpec

if TYPE_CHECKING:
    from mmc_base.contract import AnalyserInputs


@dataclass
class _MetricSet:
    """Computed metrics for one run.

    Attributes:
        scalars: Scalar metrics keyed by CW metric name.
        per_class: Per-class precision/recall/F1 dicts keyed by class label.
        confusion: Confusion matrix rows.
        class_labels: Ordered class labels for the confusion matrix.
    """

    scalars: dict[str, float] = field(default_factory=dict)
    per_class: dict[str, dict[str, float]] = field(default_factory=dict)
    confusion: list[list[int]] = field(default_factory=list)
    class_labels: list[str] = field(default_factory=list)


def _compute_scalars(y_true: pd.Series, y_pred: pd.Series) -> dict[str, float]:
    """Return accuracy + macro precision / recall / F1.

    Args:
        y_true: Ground-truth labels.
        y_pred: Predicted labels.

    Returns:
        Dict keyed by CW metric name.
    """
    return {
        "mq/accuracy": float(accuracy_score(y_true, y_pred)),
        "mq/precision_macro": float(precision_score(y_true, y_pred, average="macro", zero_division=0)),
        "mq/recall_macro": float(recall_score(y_true, y_pred, average="macro", zero_division=0)),
        "mq/f1_macro": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
    }


def _compute_per_class(
    y_true: pd.Series,
    y_pred: pd.Series,
    labels: list[Any],
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Return per-class metrics and their flattened CW form.

    Args:
        y_true: Ground-truth labels.
        y_pred: Predicted labels.
        labels: Class labels in canonical order.

    Returns:
        ``(per_class_map, cw_flat)`` — ``per_class_map`` keyed by string
        class label, ``cw_flat`` keyed by ``mq/<metric>_per_class/<label>``.
    """
    precision = precision_score(y_true, y_pred, labels=labels, average=None, zero_division=0)
    recall = recall_score(y_true, y_pred, labels=labels, average=None, zero_division=0)
    f1 = f1_score(y_true, y_pred, labels=labels, average=None, zero_division=0)

    per_class: dict[str, dict[str, float]] = {}
    cw_flat: dict[str, float] = {}
    for i, label in enumerate(labels):
        key = str(label)
        per_class[key] = {
            "precision": float(precision[i]),
            "recall": float(recall[i]),
            "f1": float(f1[i]),
        }
        cw_flat[f"mq/precision_per_class/{key}"] = float(precision[i])
        cw_flat[f"mq/recall_per_class/{key}"] = float(recall[i])
        cw_flat[f"mq/f1_per_class/{key}"] = float(f1[i])
    return per_class, cw_flat


def _compute_auc(
    y_true: pd.Series,
    df: pd.DataFrame,
    spec: MqSpec,
) -> float | None:
    """Return binary ROC AUC when probabilities are supplied.

    Args:
        y_true: Ground-truth labels.
        df: Full predictions frame (for probability column lookup).
        spec: Analyser spec.

    Returns:
        AUC value, or ``None`` when not applicable.
    """
    if spec.problem_type != "binary" or not spec.probability_columns:
        return None
    prob_col = spec.probability_columns[0]
    if prob_col not in df.columns:
        return None
    positive = spec.positive_label
    if positive is None:
        uniques = sorted(pd.unique(y_true).tolist())
        positive = uniques[-1]
    y_binary = (y_true == positive).astype(int)
    return float(roc_auc_score(y_binary, df[prob_col]))


def _compute_metrics(df: pd.DataFrame, spec: MqSpec) -> _MetricSet:
    """Compute all metrics for one predictions frame.

    Args:
        df: Predictions frame.
        spec: Analyser spec.

    Returns:
        Populated :class:`_MetricSet`.
    """
    y_true = df[spec.label_column]
    y_pred = df[spec.prediction_column]

    result = _MetricSet()
    result.scalars.update(_compute_scalars(y_true, y_pred))

    labels = sorted(set(pd.unique(y_true).tolist()) | set(pd.unique(y_pred).tolist()))
    result.class_labels = [str(label) for label in labels]
    result.confusion = confusion_matrix(y_true, y_pred, labels=labels).tolist()

    if spec.problem_type == "multiclass":
        per_class, cw_flat = _compute_per_class(y_true, y_pred, labels)
        result.per_class = per_class
        result.scalars.update(cw_flat)

    auc = _compute_auc(y_true, df, spec)
    if auc is not None:
        result.scalars["mq/auc"] = auc
    return result


def _score_violations(
    metrics: dict[str, float],
    spec: MqSpec,
) -> tuple[list[dict[str, float | str]], dict[str, float]]:
    """Compare current metrics to baseline; flag threshold breaches.

    Args:
        metrics: Current CW metric values.
        spec: Analyser spec.

    Returns:
        ``(violations, baseline_delta)`` — ``violations`` is a list of
        breach records, ``baseline_delta`` maps baseline-key to
        ``current - baseline``.
    """
    violations: list[dict[str, float | str]] = []
    baseline_delta: dict[str, float] = {}
    for name, baseline in spec.baseline_metrics.items():
        current = _lookup_current(name, metrics)
        if current is None:
            continue
        delta = current - baseline
        baseline_delta[name] = delta
        threshold = spec.degradation_thresholds.get(name)
        if threshold is None:
            continue
        if current < baseline - threshold:
            violations.append(
                {
                    "metric": name,
                    "baseline": baseline,
                    "current": current,
                    "delta": delta,
                    "threshold": threshold,
                },
            )
    return violations, baseline_delta


def _lookup_current(baseline_key: str, metrics: dict[str, float]) -> float | None:
    """Resolve a baseline key against the computed metric dict.

    Baseline keys use short names (``accuracy``, ``f1_macro``); CW metric
    keys use the ``mq/<name>`` prefix. Accept either form.

    Args:
        baseline_key: Metric name as supplied in ``baseline_metrics``.
        metrics: Computed CW metric dict.

    Returns:
        Current metric value, or ``None`` if not computed.
    """
    if baseline_key in metrics:
        return metrics[baseline_key]
    prefixed = f"mq/{baseline_key}"
    if prefixed in metrics:
        return metrics[prefixed]
    return None


class MqAnalyser:
    """Compute model-quality metrics and compare against a baseline snapshot.

    Loads a parquet predictions frame from
    ``inputs.paths[spec.predictions_input]``. Emits per-metric CloudWatch
    values under the ``mq/`` prefix and flags metrics whose degradation
    (``baseline - current``) exceeds the per-metric threshold as
    violations. Severity is ``warn`` when violations are below
    ``spec.severity_threshold`` and ``alert`` when at or above it.
    """

    def compute(self, inputs: AnalyserInputs, config: dict[str, Any]) -> AnalyserOutput:  # noqa: PLR6301 — protocol
        """Run MQ metrics against the configured predictions frame.

        Args:
            inputs: Materialised input paths.
            config: Raw config dict — parsed as :class:`MqSpec`.

        Returns:
            Validated :class:`AnalyserOutput`.
        """
        started = datetime.now(UTC)
        spec = MqSpec.model_validate(config)
        df = pd.read_parquet(inputs.paths[spec.predictions_input])

        metrics = _compute_metrics(df, spec)
        violations, baseline_delta = _score_violations(metrics.scalars, spec)

        outcome = Outcome.succeeded_with_violations if violations else Outcome.succeeded
        if not violations:
            severity = Severity.info
        elif len(violations) >= spec.severity_threshold:
            severity = Severity.alert
        else:
            severity = Severity.warn

        payload: dict[str, Any] = {
            "version": "1.0",
            "monitor_type": "MQ",
            "problem_type": spec.problem_type,
            "label": spec.label_column,
            "prediction": spec.prediction_column,
            "metrics": _payload_metrics(metrics),
            "confusion_matrix": {
                "labels": metrics.class_labels,
                "rows": metrics.confusion,
            },
            "baseline_delta": baseline_delta,
            "violations": violations,
        }

        return AnalyserOutput(
            outcome=outcome,
            severity=severity,
            violation_count=len(violations),
            analyser_metrics=metrics.scalars,
            run_started_at=started,
            payload=payload,
        )


def _payload_metrics(metrics: _MetricSet) -> dict[str, Any]:
    """Strip the ``mq/`` prefix for the payload's ``metrics`` view.

    Args:
        metrics: Computed metric set.

    Returns:
        ``{scalar_name: value, "per_class": {...}}`` — CW keys mapped back
        to short names; per-class metrics grouped separately.
    """
    prefix = "mq/"
    payload: dict[str, Any] = {}
    for key, value in metrics.scalars.items():
        if "_per_class/" in key or not key.startswith(prefix):
            continue
        payload[key.removeprefix(prefix)] = value
    if metrics.per_class:
        payload["per_class"] = metrics.per_class
    return payload
