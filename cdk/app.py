"""CDK entrypoint for model-monitor-custom.

Instantiates stacks based on ``-c target_account=<name>`` context. Real
account IDs land later via ``cdk.json`` context or a config module.
"""

from __future__ import annotations

import aws_cdk as cdk
from model_monitor_cdk.stacks.artifact_stack import ArtifactStack, ArtifactStackProps
from model_monitor_cdk.stacks.inference_monitor_stack import (
    InferenceMonitorStack,
    InferenceMonitorStackProps,
)
from model_monitor_cdk.stacks.operations_baseline_stack import (
    OperationsBaselineStack,
    OperationsBaselineStackProps,
)
from model_monitor_cdk.stacks.shared_iam_stack import SharedIamStack, SharedIamStackProps

app = cdk.App()

target_account_name = app.node.try_get_context("target_account")

# TODO(mmc-config): source real account IDs from cdk.json context
_ML_INFERENCE_ACCOUNT_TEST = "000000000000"  # placeholder
_ML_ARTIFACT_ACCOUNT = "000000000000"  # placeholder
_ML_OPERATIONS_ACCOUNT = "000000000000"  # placeholder
_ARTIFACT_KMS_KEY_ARN = "arn:aws:kms:eu-west-1:000000000000:key/placeholder"
_BASELINES_BUCKET_ARN = "arn:aws:s3:::mmc-baselines-placeholder"
_ECR_HOST = "000000000000.dkr.ecr.eu-west-1.amazonaws.com"
_ANALYSER_IMAGES: dict[str, str] = {
    name: f"{_ECR_HOST}/mmc/analyser-{name}:sha-placeholder" for name in ("mq", "dq", "bias", "explain", "shadow")
}

if target_account_name == "ml-artifact":
    artifact = ArtifactStack(
        app,
        "MMC-Test-Artifact",
        props=ArtifactStackProps(
            environment="test",
            consumer_account_ids=[_ML_INFERENCE_ACCOUNT_TEST],
            operations_account_id=_ML_OPERATIONS_ACCOUNT,
        ),
    )
    SharedIamStack(
        app,
        "MMC-Test-SharedIam",
        props=SharedIamStackProps(
            environment="test",
            reader_accounts=[_ML_INFERENCE_ACCOUNT_TEST],
            writer_account_id=_ML_OPERATIONS_ACCOUNT,
            baselines_bucket_arn=artifact.baselines_bucket.bucket_arn,
            artifact_kms_key_arn=artifact.kms_key.key_arn,
        ),
    )

if target_account_name == "ml-inference-test":
    InferenceMonitorStack(
        app,
        "MMC-Test-InferenceMonitor-Example",
        props=InferenceMonitorStackProps(
            environment="test",
            project_name="example-classifier",
            consumer_account_id=_ML_INFERENCE_ACCOUNT_TEST,
            artifact_account_id=_ML_ARTIFACT_ACCOUNT,
            artifact_kms_key_arn=_ARTIFACT_KMS_KEY_ARN,
            baselines_bucket_arn=_BASELINES_BUCKET_ARN,
            analyser_image_uris=_ANALYSER_IMAGES,
        ),
        env=cdk.Environment(account=_ML_INFERENCE_ACCOUNT_TEST, region="eu-west-1"),
    )

if target_account_name == "ml-operations":
    # TODO(mmc-config): source the writer role ARN + producer bucket ARN from
    # context once SharedIamStack outputs are wired through cdk.json.
    OperationsBaselineStack(
        app,
        "MMC-Test-OperationsBaseline-Example",
        props=OperationsBaselineStackProps(
            environment="test",
            project_name="example-classifier",
            operations_account_id=_ML_OPERATIONS_ACCOUNT,
            artifact_account_id=_ML_ARTIFACT_ACCOUNT,
            baselines_bucket_arn=_BASELINES_BUCKET_ARN,
            artifact_kms_key_arn=_ARTIFACT_KMS_KEY_ARN,
            baseline_writer_role_arn="arn:aws:iam::000000000000:role/mmc-test-baseline-writer",  # placeholder
            producer_bucket_arn="arn:aws:s3:::mmc-training-snapshots-placeholder",
            analyser_image_uris=_ANALYSER_IMAGES,
        ),
        env=cdk.Environment(account=_ML_OPERATIONS_ACCOUNT, region="eu-west-1"),
    )

app.synth()
