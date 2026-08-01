"""Baseline gate logic — manifest vs config comparison."""

from __future__ import annotations

from dataclasses import dataclass, field

from model_monitor_cdk.config import ProjectSpec
from model_monitor_cdk.manifest import Manifest


@dataclass
class GateOutcome:
    """Gate evaluation result — status, warnings, analyser plan.

    Attributes:
        status: "approved", "approved_with_warnings", or "rejected".
        message: Summary reason for the status.
        warnings: List of non-blocking issues.
        analysers_to_run: List of analyser types to execute.
    """

    status: str  # "approved" | "approved_with_warnings" | "rejected"
    message: str
    warnings: list[str] = field(default_factory=list)
    analysers_to_run: list[str] = field(default_factory=list)


class GateLogic:
    """Three-way gate: manifest vs config vs S3 objects.

    Compares what the training pipeline produced (manifest) against what
    the config requires, deciding which analysers run, warn, or fail.
    """

    def __init__(self, manifest: Manifest, project: ProjectSpec):
        """Initialize gate logic.

        Args:
            manifest: Training artifact manifest.
            project: Project config (monitors, enabled/required semantics).
        """
        self.manifest = manifest
        self.project = project

    def evaluate(self) -> GateOutcome:
        """Evaluate three-way comparison: manifest vs config.

        Returns:
            GateOutcome with status, message, warnings, analysers_to_run.

        Logic (per v2 design §2):
        1. For each enabled monitor, check if manifest has required artifacts.
        2. If config.required=true and artifact missing → hard fail.
        3. If config.required=false and artifact missing → warn, skip analyser.
        4. Return plan: which analysers run, which skip, warnings.
        """
        warnings: list[str] = []
        analysers_to_run: list[str] = []
        failed = False

        # Analyze each monitor from config
        for monitor_name, monitor_config in self.project.monitors.items():
            if not monitor_config.enabled:
                # Disabled monitors never run
                continue

            # Determine which artifacts this monitor needs
            artifacts_needed = self._artifacts_for_monitor(monitor_name)

            # Check if all needed artifacts are in manifest
            manifest_has_all = all(artifact in self.manifest.artifacts for artifact in artifacts_needed)

            if manifest_has_all:
                # All artifacts present → analyser will run
                analysers_to_run.append(monitor_name)
            # Artifact(s) missing
            elif monitor_config.required:
                # Hard fail: required artifact missing
                failed = True
                warnings.append(
                    f"Required monitor {monitor_name!r} missing artifacts: "
                    f"expected {artifacts_needed}, got {list(self.manifest.artifacts.keys())}"
                )
            else:
                # Warn but continue: optional artifact missing
                warnings.append(f"Optional monitor {monitor_name!r} missing artifacts {artifacts_needed}, will skip")

        if failed:
            return GateOutcome(
                status="rejected",
                message=f"Baseline gating failed: {len(warnings)} required artifact(s) missing",
                warnings=warnings,
                analysers_to_run=[],
            )

        status = "approved_with_warnings" if warnings else "approved"
        return GateOutcome(
            status=status,
            message="Baseline gating passed",
            warnings=warnings,
            analysers_to_run=analysers_to_run,
        )

    @staticmethod
    def _artifacts_for_monitor(monitor_name: str) -> list[str]:
        """Map monitor type to required artifact keys.

        Returns:
            List of artifact keys (from manifest) needed by this monitor.
        """
        # Simple mapping: monitor → artifacts it needs
        # In a real system, this could be more complex (thresholds, etc.)
        mapping = {
            "model_quality": ["training_snapshot", "predictions", "model"],
            "data_quality": ["training_snapshot", "evaluation_results"],
            "bias": ["training_snapshot", "evaluation_results", "model"],
            "explainability": ["training_snapshot", "model"],
            "shadow": [],  # shadow needs no baselines (live-vs-live)
        }
        return mapping.get(monitor_name, [])
