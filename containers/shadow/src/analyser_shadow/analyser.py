"""Real shadow analyser — serving-vs-shadow variant comparison.

Compares the currently-serving variant against a candidate (shadow) variant
on the same inputs. Baseline-independent: no baseline snapshot is required.

Emits per-metric CW values for hard-label agreement rate, mean Jensen-Shannon
divergence between per-row probability distributions, and per-class
disagreement rates. Zero AWS SDK calls — the base image owns all IO.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from mmc_base.contract import AnalyserOutput, Outcome, Severity

from analyser_shadow.spec import ShadowSpec

if TYPE_CHECKING:
    from mmc_base.contract import AnalyserInputs


def _align(
    serving: pd.DataFrame,
    shadow: pd.DataFrame,
    join_key: str | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(serving, shadow)`` aligned to the same rows.

    Args:
        serving: Serving predictions frame.
        shadow: Shadow predictions frame.
        join_key: Column used to align rows, or ``None`` for positional.

    Returns:
        Two frames of identical length in the same row order.

    Raises:
        ValueError: If frames cannot be aligned.
    """
    if join_key is not None:
        merged_serving = serving.set_index(join_key).sort_index()
        merged_shadow = shadow.set_index(join_key).sort_index()
        common = merged_serving.index.intersection(merged_shadow.index)
        if len(common) == 0:
            msg = f"No overlapping rows on join_key={join_key!r}"
            raise ValueError(msg)
        return merged_serving.loc[common].reset_index(), merged_shadow.loc[common].reset_index()
    if len(serving) != len(shadow):
        msg = f"Serving ({len(serving)}) and shadow ({len(shadow)}) row counts differ; supply join_key"
        raise ValueError(msg)
    return serving.reset_index(drop=True), shadow.reset_index(drop=True)


def _prob_matrix(df: pd.DataFrame, columns: list[str], problem_type: str) -> np.ndarray | None:
    """Return an ``[n_rows, n_classes]`` probability matrix, or ``None``.

    Args:
        df: Frame containing the probability columns.
        columns: Configured probability columns.
        problem_type: ``"binary"`` or ``"multiclass"``.

    Returns:
        A row-normalised probability matrix, or ``None`` if not available.
    """
    if not columns:
        return None
    missing = [c for c in columns if c not in df.columns]
    if missing:
        return None
    arr = df[columns].to_numpy(dtype=float)
    if problem_type == "binary" and arr.shape[1] == 1:
        p1 = np.clip(arr[:, 0], 0.0, 1.0)
        arr = np.column_stack([1.0 - p1, p1])
    row_sums = arr.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    return arr / row_sums


def _kl(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Return per-row KL divergence ``sum(p * log2(p / q))`` with 0 log 0 = 0.

    Args:
        p: ``[n, k]`` non-negative distribution matrix.
        q: ``[n, k]`` non-negative distribution matrix.

    Returns:
        ``[n]`` per-row KL divergence in base 2.
    """
    eps = 1e-12
    p_safe = np.clip(p, eps, 1.0)
    q_safe = np.clip(q, eps, 1.0)
    return np.sum(p_safe * (np.log2(p_safe) - np.log2(q_safe)), axis=1)


def _mean_js_divergence(p: np.ndarray, q: np.ndarray) -> float:
    """Return mean per-row Jensen-Shannon divergence in base 2 (range [0, 1]).

    JS(P, Q) = 0.5 * KL(P || M) + 0.5 * KL(Q || M) where M = 0.5 * (P + Q).

    Args:
        p: ``[n, k]`` distribution matrix.
        q: ``[n, k]`` distribution matrix.

    Returns:
        Mean JS divergence across rows.
    """
    m = 0.5 * (p + q)
    js = 0.5 * _kl(p, m) + 0.5 * _kl(q, m)
    js = np.where(np.isnan(js), 0.0, js)
    return float(np.mean(js))


def _per_class_disagreement(
    serving_pred: pd.Series,
    shadow_pred: pd.Series,
) -> dict[str, float]:
    """Return per-class fraction of rows where shadow disagrees with serving.

    Args:
        serving_pred: Serving hard-label predictions.
        shadow_pred: Shadow hard-label predictions.

    Returns:
        ``{class_name: disagreement_fraction}`` for each class predicted by
        the serving variant.
    """
    out: dict[str, float] = {}
    disagree_mask = serving_pred.to_numpy() != shadow_pred.to_numpy()
    for cls, count in serving_pred.value_counts().items():
        cls_mask = (serving_pred == cls).to_numpy()
        disagree = int(np.logical_and(cls_mask, disagree_mask).sum())
        out[str(cls)] = disagree / int(count) if int(count) > 0 else 0.0
    return out


def _distribution_summary(
    serving_pred: pd.Series,
    shadow_pred: pd.Series,
) -> dict[str, dict[str, float]]:
    """Return hard-label histograms for serving and shadow.

    Args:
        serving_pred: Serving hard-label predictions.
        shadow_pred: Shadow hard-label predictions.

    Returns:
        ``{"serving": {class: fraction}, "shadow": {class: fraction}}``.
    """
    return {
        "serving": {str(k): float(v) for k, v in serving_pred.value_counts(normalize=True).items()},
        "shadow": {str(k): float(v) for k, v in shadow_pred.value_counts(normalize=True).items()},
    }


class ShadowAnalyser:
    """Serving-vs-shadow comparison analyser.

    Loads two parquet inputs — serving-variant and shadow-variant
    predictions — aligns them (by ``join_key`` if supplied, else positional),
    and computes:

    * Hard-label **agreement rate** — fraction of rows with matching class.
    * Mean **Jensen-Shannon divergence** between per-row probability
      distributions (when probability columns are supplied).
    * Per-class **disagreement rate** — for each class serving predicts,
      the fraction of rows where shadow differs.

    Violations fire when agreement drops below ``spec.agreement_threshold``
    or mean JS divergence exceeds ``spec.js_divergence_threshold``. Severity
    escalates to ``alert`` once ``violation_count`` reaches
    ``spec.severity_threshold``; otherwise it is ``warn`` on any violation
    and ``info`` on a clean run.
    """

    def compute(self, inputs: AnalyserInputs, config: dict[str, Any]) -> AnalyserOutput:  # noqa: PLR6301, PLR0914 — protocol; unavoidable locals
        """Compute the shadow comparison.

        Args:
            inputs: Materialised input paths.
            config: Raw config dict — parsed as :class:`ShadowSpec`.

        Returns:
            Validated :class:`AnalyserOutput`.
        """
        started = datetime.now(UTC)
        spec = ShadowSpec.model_validate(config)

        serving_df = pd.read_parquet(inputs.paths[spec.serving_input])
        shadow_df = pd.read_parquet(inputs.paths[spec.shadow_input])
        serving_df, shadow_df = _align(serving_df, shadow_df, spec.join_key)

        serving_pred = serving_df[spec.prediction_column]
        shadow_pred = shadow_df[spec.prediction_column]

        agreement = float((serving_pred.to_numpy() == shadow_pred.to_numpy()).mean())
        per_class = _per_class_disagreement(serving_pred, shadow_pred)

        serving_probs = _prob_matrix(serving_df, spec.probability_columns, spec.problem_type)
        shadow_probs = _prob_matrix(shadow_df, spec.probability_columns, spec.problem_type)
        js_divergence = (
            _mean_js_divergence(serving_probs, shadow_probs)
            if serving_probs is not None and shadow_probs is not None
            else 0.0
        )

        metrics: dict[str, float] = {
            "shadow/agreement": agreement,
            "shadow/js_divergence": js_divergence,
        }
        for cls_name, rate in per_class.items():
            metrics[f"shadow/disagreement_per_class/{cls_name}"] = rate

        violations = 0
        if agreement < spec.agreement_threshold:
            violations += 1
        if js_divergence > spec.js_divergence_threshold:
            violations += 1

        if violations == 0:
            outcome, severity = Outcome.succeeded, Severity.info
        else:
            outcome = Outcome.succeeded_with_violations
            severity = Severity.alert if violations >= spec.severity_threshold else Severity.warn

        payload = {
            "version": "1.0",
            "monitor_type": "SHADOW",
            "serving_variant": spec.serving_variant,
            "shadow_variant": spec.shadow_variant,
            "problem_type": spec.problem_type,
            "row_count": len(serving_df),
            "agreement_rate": agreement,
            "js_divergence": js_divergence,
            "disagreement_per_class": per_class,
            "distribution_summary": _distribution_summary(serving_pred, shadow_pred),
        }

        return AnalyserOutput(
            outcome=outcome,
            severity=severity,
            violation_count=violations,
            analyser_metrics=metrics,
            run_started_at=started,
            payload=payload,
        )
