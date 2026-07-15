"""CDK stacks for model-monitor-custom."""

from model_monitor_cdk.stacks.artifact_stack import ArtifactStack, ArtifactStackProps
from model_monitor_cdk.stacks.github_oidc_stack import (
    GithubOidcStack,
    GithubOidcStackProps,
)
from model_monitor_cdk.stacks.inference_monitor_stack import (
    InferenceMonitorStack,
    InferenceMonitorStackProps,
)
from model_monitor_cdk.stacks.operations_baseline_stack import (
    OperationsBaselineStack,
    OperationsBaselineStackProps,
)
from model_monitor_cdk.stacks.producer_events_stack import (
    ProducerEventsStack,
    ProducerEventsStackProps,
)
from model_monitor_cdk.stacks.shared_iam_stack import SharedIamStack, SharedIamStackProps

__all__ = [
    "ArtifactStack",
    "ArtifactStackProps",
    "GithubOidcStack",
    "GithubOidcStackProps",
    "InferenceMonitorStack",
    "InferenceMonitorStackProps",
    "OperationsBaselineStack",
    "OperationsBaselineStackProps",
    "ProducerEventsStack",
    "ProducerEventsStackProps",
    "SharedIamStack",
    "SharedIamStackProps",
]
