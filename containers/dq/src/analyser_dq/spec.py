"""DqAnalyser config schema.

Pydantic v2 with ``extra="forbid"`` per ADR 004. Owns the math thresholds
that drive violation counting and severity escalation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DqSpec(BaseModel):
    """Data-quality analyser configuration.

    Attributes:
        schema_version: Semver of the spec shape.
        current_input: Name of the ``AnalyserInputs.paths`` entry holding
            the current-window parquet dataset.
        baseline_input: Name of the ``AnalyserInputs.paths`` entry holding
            the baseline snapshot parquet.
        numeric_columns: Columns whose distribution is compared via KS.
        categorical_columns: Columns whose distribution is compared via PSI.
        completeness_threshold: Minimum fraction of non-null rows per
            column; anything under triggers a completeness violation.
        ks_p_value_threshold: KS-test p-value below which numeric drift is
            treated as a violation (reject the null of same distribution).
        psi_threshold: Population Stability Index above which categorical
            drift is treated as a violation.
        severity_threshold: Total violation count at or above which the
            output severity escalates from ``warn`` to ``alert``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    current_input: str = "current"
    baseline_input: str = "baseline"
    numeric_columns: list[str] = Field(default_factory=list)
    categorical_columns: list[str] = Field(default_factory=list)
    completeness_threshold: float = Field(default=0.99, ge=0.0, le=1.0)
    ks_p_value_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    psi_threshold: float = Field(default=0.2, ge=0.0)
    severity_threshold: int = Field(default=3, ge=1)
