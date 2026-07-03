"""ShadowAnalyser config schema.

Uses Pydantic v2 with ``extra="forbid"`` per ADR 004. Shadow is
baseline-independent: it compares a serving variant vs a shadow variant on
the same inputs and needs no baseline snapshot.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProblemType = Literal["binary", "multiclass"]


class ShadowSpec(BaseModel):
    """Shadow analyser configuration.

    Attributes:
        schema_version: Semver of the spec shape.
        problem_type: ``"binary"`` or ``"multiclass"``.
        serving_variant: Name of currently-serving variant (used as CW dim).
        shadow_variant: Candidate variant name.
        serving_input: Name of the input in ``AnalyserInputs.paths`` holding
            serving-variant predictions (parquet).
        shadow_input: Name of the input in ``AnalyserInputs.paths`` holding
            shadow-variant predictions (parquet).
        prediction_column: Column holding the hard-label prediction.
        probability_columns: Ordered list of per-class probability columns.
            Required when problem_type is ``multiclass``; for ``binary`` a
            single column is acceptable and treated as P(class=1).
        join_key: Optional column used to align serving and shadow rows.
            When absent, rows are aligned by position.
        agreement_threshold: Violation when hard-label agreement falls below
            this fraction.
        js_divergence_threshold: Violation when mean per-row Jensen-Shannon
            divergence exceeds this value.
        severity_threshold: Minimum violation count before severity escalates
            to ``alert``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    problem_type: ProblemType
    serving_variant: str
    shadow_variant: str
    serving_input: str = "serving_predictions"
    shadow_input: str = "shadow_predictions"
    prediction_column: str = "prediction"
    probability_columns: list[str] = Field(default_factory=list)
    join_key: str | None = None
    agreement_threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    js_divergence_threshold: float = Field(default=0.1, ge=0.0)
    severity_threshold: int = Field(default=2, ge=0)
