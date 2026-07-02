"""CDK entrypoint for model-monitor-custom.

Instantiates stacks based on ``-c target_account=<name>`` context. Real
account IDs land later via ``cdk.json`` context or a config module.
"""

from __future__ import annotations

from aws_cdk import App
from model_monitor_cdk.stacks.artifact_stack import ArtifactStack, ArtifactStackProps

app = App()

target_account_name = app.node.try_get_context("target_account")

if target_account_name == "ml-artifact":
    # TODO(mmc-config): source from cdk.json context
    ArtifactStack(
        app,
        "MMC-Test-Artifact",
        props=ArtifactStackProps(
            environment="test",
            consumer_account_ids=["000000000001", "000000000002"],
            operations_account_id="000000000003",
        ),
    )

app.synth()
