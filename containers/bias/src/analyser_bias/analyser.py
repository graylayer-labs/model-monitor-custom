"""Real bias analyser — smclarify wrapper.

Loads a parquet dataset, computes pre-training (and optional post-training)
bias metrics per facet via :func:`smclarify.bias.report.bias_report`, and
returns a Clarify-compatible payload plus per-metric CloudWatch values.

Zero AWS SDK calls — the base image owns all IO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pandas as pd
from mmc_base.contract import AnalyserOutput, Outcome, Severity
from smclarify.bias.report import FacetColumn, LabelColumn, StageType, bias_report

from analyser_bias.spec import POST_TRAINING_METHODS, PRE_TRAINING_METHODS, BiasSpec

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mmc_base.contract import AnalyserInputs


def _coerce_string_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with pandas ``StringDtype`` columns cast to ``object``.

    smclarify's ``ensure_series_data_type`` fails on the arrow-backed
    ``StringDtype`` that ``read_parquet`` returns; ``object`` works.

    Args:
        df: Input frame.

    Returns:
        Frame with string columns downcast to ``object`` dtype.
    """
    out = df.copy()
    for col in out.select_dtypes(include="string").columns:
        out[col] = out[col].astype(object)
    return out


def _split_methods(methods: Iterable[str]) -> tuple[list[str], list[str]]:
    """Partition metric codes into (pre-training, post-training).

    Args:
        methods: Requested metric codes from the spec.

    Returns:
        ``(pre_methods, post_methods)``.
    """
    pre = [m for m in methods if m in PRE_TRAINING_METHODS]
    post = [m for m in methods if m in POST_TRAINING_METHODS]
    return pre, post


def _run_stage(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] — stage args are all required by bias_report
    df: pd.DataFrame,
    facet: FacetColumn,
    label: LabelColumn,
    stage: StageType,
    methods: list[str],
    predicted: LabelColumn | None,
) -> list[dict[str, Any]]:
    """Invoke ``bias_report`` for one facet + stage combo.

    Args:
        df: Dataset frame.
        facet: Facet column spec.
        label: Label column spec.
        stage: Pre- or post-training.
        methods: Metric codes to compute for this stage.
        predicted: Predicted-label column (post-training only).

    Returns:
        smclarify's list-of-facet-result dicts (may be empty).
    """
    if not methods:
        return []
    return bias_report(
        df,
        facet,
        label,
        stage,
        predicted_label_column=predicted,
        metrics=methods,
    )


def _cw_key(facet_name: str, stage: str, metric_name: str) -> str:
    """Build the CloudWatch ``MetricName`` dim value for one metric.

    Format: ``"<facet>/<stage>/<metric>"`` — ``stage`` is ``pre`` or
    ``post`` so the base's ``MetricName`` CW dim stays flat and unique
    across facets and stages.

    Args:
        facet_name: Facet column name.
        stage: ``"pre"`` or ``"post"``.
        metric_name: Bias metric code.

    Returns:
        Composite CW metric key.
    """
    return f"{facet_name}/{stage}/{metric_name}"


@dataclass
class _RunState:
    """Mutable accumulator for the per-facet loop.

    Attributes:
        metrics_cw: Per-metric CloudWatch values keyed by ``<facet>/<stage>/<name>``.
        pre_facets: Pre-training metrics grouped by facet name.
        post_facets: Post-training metrics grouped by facet name.
        errors: Per-metric error records from smclarify.
        violation_count: Number of metrics past their configured threshold.
        alert_hit: Whether any metric crossed its ``severity_alert_thresholds`` cutoff.
    """

    metrics_cw: dict[str, float] = field(default_factory=dict)
    pre_facets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    post_facets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)
    violation_count: int = 0
    alert_hit: bool = False


def _score_metric(
    value: float,
    name: str,
    spec: BiasSpec,
) -> tuple[bool, bool]:
    """Return ``(is_violation, is_alert)`` for one metric.

    Args:
        value: Metric value.
        name: Metric code.
        spec: Bias spec (thresholds live here).

    Returns:
        Tuple of violation flag and alert flag.
    """
    threshold = spec.thresholds.get(name)
    alert = (spec.severity_alert_thresholds or {}).get(name)
    absval = abs(value)
    return (threshold is not None and absval > threshold), (alert is not None and absval > alert)


def _process_facet(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] — one call site; splitting hurts readability
    df: pd.DataFrame,
    facet_spec: Any,  # ruff: ignore[any-type] — Facet from spec module
    spec: BiasSpec,
    label: LabelColumn,
    predicted: LabelColumn | None,
    pre_methods: list[str],
    post_methods: list[str],
    state: _RunState,
) -> None:
    """Compute bias metrics for one facet and fold into ``state``.

    Args:
        df: Dataset frame.
        facet_spec: Facet config (name + sensitive values).
        spec: Full bias spec (for thresholds).
        label: Label column spec.
        predicted: Predicted-label column, if any.
        pre_methods: Pre-training metric codes.
        post_methods: Post-training metric codes.
        state: Accumulator; mutated in place.
    """
    facet = FacetColumn(facet_spec.name, list(facet_spec.values))
    pre_flat = _flatten_metrics(_run_stage(df, facet, label, StageType.PRE_TRAINING, pre_methods, None))
    post_flat = _flatten_metrics(
        _run_stage(df, facet, label, StageType.POST_TRAINING, post_methods, predicted) if predicted is not None else [],
    )
    state.pre_facets[facet_spec.name] = pre_flat["metrics"]
    if post_flat["metrics"]:
        state.post_facets[facet_spec.name] = post_flat["metrics"]
    state.errors.extend({"facet": facet_spec.name, **e} for e in pre_flat["errors"] + post_flat["errors"])

    for stage_tag, flat in (("pre", pre_flat), ("post", post_flat)):
        for entry in flat["metrics"]:
            value = float(entry["value"])
            state.metrics_cw[_cw_key(facet_spec.name, stage_tag, entry["name"])] = value
            is_viol, is_alert = _score_metric(value, entry["name"], spec)
            if is_viol:
                state.violation_count += 1
            if is_alert:
                state.alert_hit = True


class BiasAnalyser:
    """smclarify-backed bias analyser.

    Loads a single parquet dataset from
    ``inputs.paths[spec.dataset_input]``. For each facet, computes the
    configured pre-training bias metrics (and post-training metrics when
    ``predicted_label_column`` is set) via smclarify's ``bias_report``.

    Emits one CloudWatch metric per (facet, stage, method) tuple keyed
    ``"<facet>/<stage>/<method>"``. A metric whose absolute value exceeds
    its per-method threshold in ``spec.thresholds`` counts as a violation
    and lifts severity to ``warn``; exceeding
    ``spec.severity_alert_thresholds`` escalates to ``alert``. When any
    violation fires, outcome is ``succeeded_with_violations``; otherwise
    ``succeeded``.
    """

    def compute(self, inputs: AnalyserInputs, config: dict[str, Any]) -> AnalyserOutput:  # ruff: ignore[no-self-use] — protocol
        """Run bias metrics against the configured dataset.

        Args:
            inputs: Materialised input paths.
            config: Raw config dict — parsed as :class:`BiasSpec`.

        Returns:
            Validated :class:`AnalyserOutput`.
        """
        started = datetime.now(UTC)
        spec = BiasSpec.model_validate(config)
        dataset_path = inputs.paths[spec.dataset_input]
        df = _coerce_string_dtypes(pd.read_parquet(dataset_path))

        label = LabelColumn(spec.label_column, df[spec.label_column], list(spec.positive_label_values))
        predicted = (
            LabelColumn(
                spec.predicted_label_column,
                df[spec.predicted_label_column],
                list(spec.positive_label_values),
            )
            if spec.predicted_label_column
            else None
        )
        pre_methods, post_methods = _split_methods(spec.methods)
        state = _RunState()
        for facet_spec in spec.facets:
            _process_facet(df, facet_spec, spec, label, predicted, pre_methods, post_methods, state)

        outcome = Outcome.succeeded_with_violations if state.violation_count > 0 else Outcome.succeeded
        if state.alert_hit:
            severity = Severity.alert
        elif state.violation_count > 0:
            severity = Severity.warn
        else:
            severity = Severity.info

        payload = {
            "version": "1.0",
            "monitor_type": "BIAS",
            "label": spec.label_column,
            "label_value_or_threshold": ",".join(str(v) for v in spec.positive_label_values),
            "pre_training_bias": {"facets": state.pre_facets},
            "post_training_bias": {"facets": state.post_facets} if state.post_facets else None,
            "errors": state.errors,
        }

        return AnalyserOutput(
            outcome=outcome,
            severity=severity,
            violation_count=state.violation_count,
            analyser_metrics=state.metrics_cw,
            run_started_at=started,
            payload=payload,
        )


def _flatten_metrics(report_result: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Flatten smclarify's list-of-facet-value dicts into metrics + errors.

    Args:
        report_result: Raw return value of :func:`bias_report`.

    Returns:
        ``{"metrics": [{name, value}, ...], "errors": [{name, error}, ...]}``.
    """
    metrics: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for facet_result in report_result:
        value_or_threshold = str(facet_result.get("value_or_threshold", ""))
        for entry in facet_result.get("metrics", []):
            name = entry["name"]
            if entry.get("error") is not None:
                errors.append({"name": name, "error": str(entry["error"])})
                continue
            value = entry.get("value")
            if value is None:
                continue
            metrics.append({"name": name, "value": float(value), "value_or_threshold": value_or_threshold})
    return {"metrics": metrics, "errors": errors}
