"""Real DQ analyser — own the math.

Loads a current-window parquet and a baseline snapshot parquet, then
computes column-level data-quality signals:

- **Schema diff** — extra / missing columns vs baseline.
- **Completeness** — fraction of non-null values per column.
- **Numeric drift** — two-sample KS test per numeric column.
- **Categorical drift** — Population Stability Index per categorical column.

Each finding contributes a CloudWatch metric under ``dq/<col>/<stat>`` and
optionally a violation. Severity is ``alert`` when the violation count
meets ``spec.severity_threshold``, ``warn`` when non-zero but below, and
``info`` when clean.

Zero AWS SDK calls — the base image owns all IO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from mmc_base.contract import AnalyserOutput, Outcome, Severity
from scipy.stats import ks_2samp

from analyser_dq.spec import DqSpec

if TYPE_CHECKING:
    from mmc_base.contract import AnalyserInputs


_PSI_EPS = 1e-6


@dataclass
class _RunState:
    """Mutable accumulator for the per-column checks.

    Attributes:
        metrics: Per-column CloudWatch values keyed ``dq/<col>/<stat>``.
        completeness: ``{col: fraction_non_null}``.
        drift: Per-column drift stats — numeric and categorical.
        violations: Structured violation records.
    """

    metrics: dict[str, float] = field(default_factory=dict)
    completeness: dict[str, float] = field(default_factory=dict)
    drift: dict[str, dict[str, float]] = field(default_factory=dict)
    violations: list[dict[str, Any]] = field(default_factory=list)


def _schema_diff(current: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, list[str]]:
    """Return columns present on one side but not the other.

    Args:
        current: Current-window frame.
        baseline: Baseline snapshot frame.

    Returns:
        ``{"missing": [...], "extra": [...]}`` — ``missing`` are baseline
        columns absent from ``current``; ``extra`` are current-only columns.
    """
    cur, base = set(current.columns), set(baseline.columns)
    return {"missing": sorted(base - cur), "extra": sorted(cur - base)}


def _psi(current: pd.Series, baseline: pd.Series) -> float:
    """Compute the Population Stability Index for two categorical series.

    PSI = ``sum((p_cur - p_base) * ln(p_cur / p_base))`` over the union of
    category labels. Zero probabilities are floored at ``_PSI_EPS`` to keep
    the log finite.

    Args:
        current: Current-window series.
        baseline: Baseline snapshot series.

    Returns:
        The PSI value. Larger = more distribution shift.
    """
    cur_counts = current.value_counts(dropna=False)
    base_counts = baseline.value_counts(dropna=False)
    categories = cur_counts.index.union(base_counts.index)
    cur_total = max(int(cur_counts.sum()), 1)
    base_total = max(int(base_counts.sum()), 1)
    psi = 0.0
    for cat in categories:
        p_cur = max(float(cur_counts.get(cat, 0)) / cur_total, _PSI_EPS)
        p_base = max(float(base_counts.get(cat, 0)) / base_total, _PSI_EPS)
        psi += (p_cur - p_base) * float(np.log(p_cur / p_base))
    return psi


def _check_completeness(current: pd.DataFrame, columns: list[str], spec: DqSpec, state: _RunState) -> None:
    """Score completeness for every configured column.

    Args:
        current: Current-window frame.
        columns: Columns to evaluate.
        spec: Analyser spec (holds the threshold).
        state: Accumulator; mutated in place.
    """
    for col in columns:
        if col not in current.columns:
            continue
        total = len(current)
        non_null = int(current[col].notna().sum())
        frac = non_null / total if total else 0.0
        state.completeness[col] = frac
        state.metrics[f"dq/{col}/completeness"] = frac
        if frac < spec.completeness_threshold:
            state.violations.append(
                {
                    "type": "completeness",
                    "column": col,
                    "value": frac,
                    "threshold": spec.completeness_threshold,
                },
            )


def _check_numeric_drift(
    current: pd.DataFrame,
    baseline: pd.DataFrame,
    spec: DqSpec,
    state: _RunState,
) -> None:
    """Score numeric drift for every configured numeric column.

    Args:
        current: Current-window frame.
        baseline: Baseline snapshot frame.
        spec: Analyser spec (holds the p-value threshold).
        state: Accumulator; mutated in place.
    """
    for col in spec.numeric_columns:
        if col not in current.columns or col not in baseline.columns:
            continue
        cur = pd.to_numeric(current[col], errors="coerce").dropna()
        base = pd.to_numeric(baseline[col], errors="coerce").dropna()
        if cur.empty or base.empty:
            continue
        stat, p_value = ks_2samp(cur.to_numpy(), base.to_numpy())
        stat_f, p_f = float(stat), float(p_value)
        state.drift[col] = {"ks_stat": stat_f, "ks_p": p_f}
        state.metrics[f"dq/{col}/ks_stat"] = stat_f
        state.metrics[f"dq/{col}/ks_p"] = p_f
        if p_f < spec.ks_p_value_threshold:
            state.violations.append(
                {
                    "type": "numeric_drift",
                    "column": col,
                    "ks_stat": stat_f,
                    "ks_p": p_f,
                    "threshold": spec.ks_p_value_threshold,
                },
            )


def _check_categorical_drift(
    current: pd.DataFrame,
    baseline: pd.DataFrame,
    spec: DqSpec,
    state: _RunState,
) -> None:
    """Score categorical drift for every configured categorical column.

    Args:
        current: Current-window frame.
        baseline: Baseline snapshot frame.
        spec: Analyser spec (holds the PSI threshold).
        state: Accumulator; mutated in place.
    """
    for col in spec.categorical_columns:
        if col not in current.columns or col not in baseline.columns:
            continue
        psi = _psi(current[col], baseline[col])
        state.drift[col] = {"psi": psi}
        state.metrics[f"dq/{col}/psi"] = psi
        if psi > spec.psi_threshold:
            state.violations.append(
                {
                    "type": "categorical_drift",
                    "column": col,
                    "psi": psi,
                    "threshold": spec.psi_threshold,
                },
            )


def _record_schema_violations(schema_diff: dict[str, list[str]], state: _RunState) -> None:
    """Append one violation per missing / extra column.

    Args:
        schema_diff: Output of :func:`_schema_diff`.
        state: Accumulator; mutated in place.
    """
    for col in schema_diff["missing"]:
        state.violations.append({"type": "schema_missing", "column": col})
    for col in schema_diff["extra"]:
        state.violations.append({"type": "schema_extra", "column": col})


def _resolve_severity(violation_count: int, threshold: int) -> Severity:
    """Return severity from violation count.

    Args:
        violation_count: Total violations recorded.
        threshold: Alert cutoff from the spec.

    Returns:
        ``alert`` if at/above threshold, ``warn`` if any, else ``info``.
    """
    if violation_count >= threshold:
        return Severity.alert
    if violation_count > 0:
        return Severity.warn
    return Severity.info


class DqAnalyser:
    """Column-level data-quality analyser.

    Loads ``current`` and ``baseline`` parquet inputs from
    ``AnalyserInputs.paths`` (keys are configurable in
    :class:`DqSpec`) and reports schema, completeness, and distribution
    drift. Owns the math.
    """

    def compute(self, inputs: AnalyserInputs, config: dict[str, Any]) -> AnalyserOutput:  # ruff: ignore[no-self-use] — protocol
        """Run DQ checks and return a structured output.

        Args:
            inputs: Materialised input paths.
            config: Raw config dict — parsed as :class:`DqSpec`.

        Returns:
            Validated :class:`AnalyserOutput`.
        """
        started = datetime.now(UTC)
        spec = DqSpec.model_validate(config)

        current = pd.read_parquet(inputs.paths[spec.current_input])
        baseline = pd.read_parquet(inputs.paths[spec.baseline_input])

        state = _RunState()
        schema_diff = _schema_diff(current, baseline)
        _record_schema_violations(schema_diff, state)

        completeness_columns = spec.numeric_columns + spec.categorical_columns
        _check_completeness(current, completeness_columns, spec, state)
        _check_numeric_drift(current, baseline, spec, state)
        _check_categorical_drift(current, baseline, spec, state)

        violation_count = len(state.violations)
        outcome = Outcome.succeeded_with_violations if violation_count > 0 else Outcome.succeeded
        severity = _resolve_severity(violation_count, spec.severity_threshold)

        payload = {
            "version": "1.0",
            "monitor_type": "DATA_QUALITY",
            "schema_diff": schema_diff,
            "completeness": state.completeness,
            "drift": state.drift,
            "violations": state.violations,
            "thresholds": {
                "completeness": spec.completeness_threshold,
                "ks_p_value": spec.ks_p_value_threshold,
                "psi": spec.psi_threshold,
                "severity": spec.severity_threshold,
            },
        }

        return AnalyserOutput(
            outcome=outcome,
            severity=severity,
            violation_count=violation_count,
            analyser_metrics=state.metrics,
            run_started_at=started,
            payload=payload,
        )
