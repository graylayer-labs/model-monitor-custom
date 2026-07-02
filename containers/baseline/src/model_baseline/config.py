"""Container runtime configuration schema."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

S3_URI_PATTERN = r"^s3://[^/]+/.+"


class BaselineConfig(BaseModel):
    """Validated configuration for a baseline container invocation.

    Attributes:
        package_group_name: SageMaker Model Package Group name.
        baseline_version: Positive integer version identifier.
        monitor_type: Either ``"BIAS"`` or ``"EXPLAINABILITY"``.
        input_s3_uri: S3 URI to the input dataset parquet.
        config_s3_uri: S3 URI to the analyzer spec (bias or explain).
        output_s3_uri: S3 URI where `analysis.json` will be written.
        model_s3_uri: Optional S3 URI to the model artefact. Required
            when ``monitor_type == "EXPLAINABILITY"``.
    """

    model_config = ConfigDict(extra="forbid")

    package_group_name: str
    baseline_version: int = Field(gt=0)
    monitor_type: Literal["BIAS", "EXPLAINABILITY"]
    input_s3_uri: str = Field(pattern=S3_URI_PATTERN)
    config_s3_uri: str = Field(pattern=S3_URI_PATTERN)
    output_s3_uri: str = Field(pattern=S3_URI_PATTERN)
    model_s3_uri: str | None = Field(default=None, pattern=S3_URI_PATTERN)

    @model_validator(mode="after")
    def _model_uri_required_for_explainability(self) -> BaselineConfig:
        """Enforce that explainability runs supply a model artefact URI.

        Returns:
            The validated model instance.

        Raises:
            ValueError: If ``monitor_type == "EXPLAINABILITY"`` but
                ``model_s3_uri`` is unset.
        """
        if self.monitor_type == "EXPLAINABILITY" and self.model_s3_uri is None:
            msg = "model_s3_uri is required when monitor_type == 'EXPLAINABILITY'"
            raise ValueError(msg)
        return self
