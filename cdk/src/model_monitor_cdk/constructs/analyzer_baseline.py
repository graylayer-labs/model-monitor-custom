"""Props dataclass for the AnalyzerBaseline construct.

The construct body itself is Phase 2 territory. Phase 1 only fixes the
public props shape so downstream code can be written against it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ECR_PATTERN = re.compile(r"^\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^\s:]+:[^\s]+$")
_ACCOUNT_ID_PATTERN = re.compile(r"^\d{12}$")


@dataclass(frozen=True, kw_only=True)
class AnalyzerBaselineProps:
    """Configuration for an AnalyzerBaseline construct.

    Attributes:
        image_uri: ECR URI for the baseline container image.
        execution_role_arn: SageMaker execution role ARN.
        baselines_bucket_name: S3 bucket holding baseline outputs.
        baselines_bucket_account_id: Account owning the baselines bucket
            (enables cross-account grants).
        input_event_bus: EventBridge bus name to publish run events on.
        sfn_state_machine_name_prefix: Prefix for the Step Functions state
            machine name.
        retry_backoff_seconds: Backoff for step retries.
        max_retries: Max retries on the compute step.
    """

    image_uri: str
    execution_role_arn: str
    baselines_bucket_name: str
    baselines_bucket_account_id: str
    input_event_bus: str = "default"
    sfn_state_machine_name_prefix: str = "baseline"
    retry_backoff_seconds: int = 30
    max_retries: int = 3

    def __post_init__(self) -> None:
        """Validate props on construction.

        Raises:
            ValueError: If any field fails validation.
        """
        if not self.image_uri or not _ECR_PATTERN.match(self.image_uri):
            msg = f"image_uri must be an ECR URI, got: {self.image_uri!r}"
            raise ValueError(msg)
        if not self.execution_role_arn:
            msg = "execution_role_arn must be a non-empty string"
            raise ValueError(msg)
        if not self.baselines_bucket_name:
            msg = "baselines_bucket_name must be a non-empty string"
            raise ValueError(msg)
        if not _ACCOUNT_ID_PATTERN.match(self.baselines_bucket_account_id):
            msg = f"baselines_bucket_account_id must be 12 digits, got: {self.baselines_bucket_account_id!r}"
            raise ValueError(msg)
