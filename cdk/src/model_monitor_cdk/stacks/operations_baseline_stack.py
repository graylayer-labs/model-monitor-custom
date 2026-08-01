"""OperationsBaselineStack — snapshot analyser runtime in the ml-operations account.

Mirrors :class:`InferenceMonitorStack` for the offline baseline path: an S3
``Object Created`` event on the producer prefix starts a Step Functions
Standard state machine whose top-level ``Parallel`` fans out over the five
analyser branches on ECS Fargate. Each branch writes its snapshot artifact
to the baselines bucket in ``ml-artifact`` by assuming the writer role from
:class:`SharedIamStack` (per ADR 008) — no direct cross-account bucket
grants. No DDB and no Pipes: snapshots are point-in-time file artifacts,
not a live changelog.

See ADR ``001-iac-layout`` (stack layout), ADR ``006-observability-contract``
(CW namespace, log group naming), ADR ``007-failure-taxonomy`` (per-branch
Retry/Catch), and ADR ``008-baseline-iam-boundary`` (assume-role writer).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_ecs as ecs,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as events_targets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_kms as kms,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_stepfunctions as sfn,
)
from constructs import Construct

_ECR_PATTERN = re.compile(r"^\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^\s:]+:[^\s]+$")
_ACCOUNT_ID_PATTERN = re.compile(r"^\d{12}$")

_ANALYSER_TYPES: tuple[str, ...] = ("mq", "dq", "bias", "explain", "shadow")

_ANALYSER_MODULES: dict[str, str] = {
    "mq": "analyser_mq.analyser:MqAnalyser",
    "dq": "analyser_dq.analyser:DqAnalyser",
    "bias": "analyser_bias.analyser:BiasAnalyser",
    "explain": "analyser_explain.analyser:ExplainAnalyser",
    "shadow": "analyser_shadow.analyser:ShadowAnalyser",
}


def _bucket_name_from_arn(bucket_arn: str) -> str:
    """Extract bucket name from an ``arn:aws:s3:::<name>[/<prefix>]`` ARN.

    Returns:
        The bucket name (segment after ``arn:aws:s3:::`` and before any ``/``).

    Raises:
        ValueError: If the ARN is malformed.
    """
    prefix = "arn:aws:s3:::"
    if not bucket_arn.startswith(prefix):
        msg = f"expected an s3 bucket ARN, got: {bucket_arn!r}"
        raise ValueError(msg)
    tail = bucket_arn[len(prefix) :]
    return tail.split("/", 1)[0]


@dataclass(frozen=True, kw_only=True)
class OperationsBaselineStackProps:
    """Configuration for :class:`OperationsBaselineStack`.

    Attributes:
        environment: Deployment environment tag (``test``/``prod``/``dev``).
        project_name: Model / silo id used in naming and tags.
        operations_account_id: 12-digit id of the ml-operations account.
        artifact_account_id: 12-digit id of the ml-artifact account.
        baselines_bucket_arn: Cross-account write target (in ml-artifact).
        artifact_kms_key_arn: Cross-account KMS key ARN (baselines bucket).
        baseline_writer_role_arn: SharedIamStack writer role; the SFN task
            role assumes this for the cross-account write.
        producer_bucket_arn: Producer bucket ARN (training snapshots).
        producer_account_id: Optional 12-digit ID of the account that owns
            the producer bucket. When set and different from
            ``operations_account_id``, the stack adds a resource-based policy
            on the default EventBridge bus granting ``events:PutEvents`` from
            that account, enabling cross-account event forwarding (ADR 010).
            ``None`` → same account (default), no bus policy added.
        producer_prefix: S3 key prefix for the ``Object Created`` filter.
        analyser_image_uris: ECR URIs keyed by analyser type
            (``mq``/``dq``/``bias``/``explain``/``shadow``).
        fargate_cpu: vCPU units for each analyser task.
        fargate_memory_mib: Memory (MiB) for each analyser task.
        sfn_retry_max_attempts: Max retries per Parallel branch.
        sfn_retry_backoff_seconds: IntervalSeconds seed for exponential backoff.
    """

    _REQUIRED_ANALYSERS: ClassVar[frozenset[str]] = frozenset(_ANALYSER_TYPES)

    environment: Literal["test", "prod", "dev"]
    project_name: str
    operations_account_id: str
    artifact_account_id: str
    baselines_bucket_arn: str
    artifact_kms_key_arn: str
    baseline_writer_role_arn: str
    producer_bucket_arn: str
    producer_account_id: str | None = None
    producer_prefix: str = "training-snapshots/"
    analyser_image_uris: dict[str, str] = field(default_factory=dict)
    vpc_id: str | None = None
    fargate_cpu: int = 1024
    fargate_memory_mib: int = 4096
    sfn_retry_max_attempts: int = 3
    sfn_retry_backoff_seconds: int = 30

    def __post_init__(self) -> None:
        """Validate props at construction."""
        self._validate_identifiers()
        self._validate_images()
        self._validate_numeric()

    def _validate_identifiers(self) -> None:
        """Check names, account IDs, and ARNs.

        Raises:
            ValueError: If any identifier is empty or malformed.
        """
        if not self.project_name:
            msg = "project_name must be a non-empty string"
            raise ValueError(msg)
        if not _ACCOUNT_ID_PATTERN.match(self.operations_account_id):
            msg = f"operations_account_id must be 12 digits, got: {self.operations_account_id!r}"
            raise ValueError(msg)
        if not _ACCOUNT_ID_PATTERN.match(self.artifact_account_id):
            msg = f"artifact_account_id must be 12 digits, got: {self.artifact_account_id!r}"
            raise ValueError(msg)
        if not self.baselines_bucket_arn:
            msg = "baselines_bucket_arn must be non-empty"
            raise ValueError(msg)
        if not self.artifact_kms_key_arn:
            msg = "artifact_kms_key_arn must be non-empty"
            raise ValueError(msg)
        if not self.baseline_writer_role_arn:
            msg = "baseline_writer_role_arn must be non-empty"
            raise ValueError(msg)
        if not self.producer_bucket_arn:
            msg = "producer_bucket_arn must be non-empty"
            raise ValueError(msg)
        if self.producer_account_id is not None and not _ACCOUNT_ID_PATTERN.match(self.producer_account_id):
            msg = f"producer_account_id must be null or 12 digits, got: {self.producer_account_id!r}"
            raise ValueError(msg)
        if not self.producer_prefix:
            msg = "producer_prefix must be non-empty"
            raise ValueError(msg)

    def _validate_images(self) -> None:
        """Check that every analyser has a well-formed ECR URI.

        Raises:
            ValueError: If keys are missing/extra or a URI isn't an ECR URI.
        """
        missing = self._REQUIRED_ANALYSERS - set(self.analyser_image_uris)
        if missing:
            msg = f"analyser_image_uris missing keys: {sorted(missing)}"
            raise ValueError(msg)
        for key, uri in self.analyser_image_uris.items():
            if key not in self._REQUIRED_ANALYSERS:
                msg = f"analyser_image_uris has unexpected key {key!r}"
                raise ValueError(msg)
            if not _ECR_PATTERN.match(uri):
                msg = f"analyser_image_uris[{key!r}] must be an ECR URI, got: {uri!r}"
                raise ValueError(msg)

    def _validate_numeric(self) -> None:
        """Check tunables are strictly positive (or non-negative for retries).

        Raises:
            ValueError: If any numeric prop is out of range.
        """
        if self.fargate_cpu <= 0:
            msg = "fargate_cpu must be positive"
            raise ValueError(msg)
        if self.fargate_memory_mib <= 0:
            msg = "fargate_memory_mib must be positive"
            raise ValueError(msg)
        if self.sfn_retry_max_attempts < 0:
            msg = "sfn_retry_max_attempts must be non-negative"
            raise ValueError(msg)
        if self.sfn_retry_backoff_seconds <= 0:
            msg = "sfn_retry_backoff_seconds must be positive"
            raise ValueError(msg)


class OperationsBaselineStack(Stack):
    """Snapshot analyser runtime; deploys once per project in ``ml-operations``."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: OperationsBaselineStackProps,
        **kwargs: Any,  # ruff: ignore[any-type]
    ) -> None:
        """Wire the snapshot analyser runtime.

        Args:
            scope: Parent construct.
            construct_id: CDK construct id.
            props: Validated configuration.
            **kwargs: Passed through to :class:`aws_cdk.Stack`.
        """
        super().__init__(scope, construct_id, **kwargs)
        self._props = props

        Tags.of(self).add("Component", "baseline")
        Tags.of(self).add("Project", props.project_name)
        Tags.of(self).add("Environment", props.environment)

        env = props.environment

        kms_key = kms.Key.from_key_arn(self, "ArtifactKmsKey", key_arn=props.artifact_kms_key_arn)
        producer_bucket = s3.Bucket.from_bucket_arn(
            self,
            "ProducerBucket",
            bucket_arn=props.producer_bucket_arn,
        )

        vpc: ec2.IVpc = (
            ec2.Vpc.from_lookup(self, "AnalyserVpc", vpc_id=props.vpc_id)
            if props.vpc_id
            else ec2.Vpc(self, "AnalyserVpc", max_azs=2, nat_gateways=0)
        )
        cluster = ecs.Cluster(
            self,
            "AnalyserCluster",
            cluster_name=f"mmc-{env}-baseline",
            vpc=vpc,
            enable_fargate_capacity_providers=True,
        )

        self.registry_table = dynamodb.Table(
            self,
            "BaselineRegistry",
            table_name=f"mmc-{env}-baseline-registry",
            partition_key=dynamodb.Attribute(name="project", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        baselines_bucket = s3.Bucket.from_bucket_arn(
            self,
            "BaselinesBucket",
            bucket_arn=props.baselines_bucket_arn,
        )

        # Build baseline Lambdas first (needed by state machine wiring)
        self._build_baseline_lambdas(
            env=env,
            baselines_bucket=baselines_bucket,
            kms_key=kms_key,
        )

        log_groups = self._build_log_groups(env)
        task_defs = self._build_task_definitions(
            env=env,
            producer_bucket=producer_bucket,
            kms_key=kms_key,
            log_groups=log_groups,
        )

        state_machine = self._build_state_machine(env=env, task_defs=task_defs, cluster=cluster)

        rule = self._build_event_rule(env=env, state_machine=state_machine)

        self._maybe_grant_cross_account_put_events(env=env)

        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
        CfnOutput(self, "EventRuleArn", value=rule.rule_arn)
        CfnOutput(self, "BaselineRegistryTableName", value=self.registry_table.table_name)
        for analyser, lg in log_groups.items():
            CfnOutput(self, f"LogGroup{analyser.title()}Name", value=lg.log_group_name)

    def _build_log_groups(self, env: str) -> dict[str, logs.LogGroup]:
        """Create ``/mmc/<env>/baseline-<analyser>`` log groups per ADR 006.

        Returns:
            Log group keyed by analyser type.
        """
        return {
            analyser: logs.LogGroup(
                self,
                f"LogGroup{analyser.title()}",
                log_group_name=f"/mmc/{env}/baseline-{analyser}",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            )
            for analyser in _ANALYSER_TYPES
        }

    def _build_task_definitions(
        self,
        *,
        env: str,
        producer_bucket: s3.IBucket,
        kms_key: kms.IKey,
        log_groups: dict[str, logs.LogGroup],
    ) -> dict[str, ecs.FargateTaskDefinition]:
        """One Fargate task definition per analyser, with a least-privilege task role.

        Cross-account write is via ``sts:AssumeRole`` on
        ``baseline_writer_role_arn`` — the task role never gets direct
        ``s3:PutObject`` on the baselines bucket (ADR 008 boundary).

        Returns:
            Task definition keyed by analyser type.
        """
        props = self._props
        defs: dict[str, ecs.FargateTaskDefinition] = {}
        for analyser in _ANALYSER_TYPES:
            task_role = iam.Role(
                self,
                f"TaskRole{analyser.title()}",
                assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),  # ty: ignore[invalid-argument-type]
                description=f"Fargate task role for mmc baseline-{analyser} analyser",
            )
            producer_bucket.grant_read(task_role, f"{props.producer_prefix}*")
            task_role.add_to_principal_policy(
                iam.PolicyStatement(
                    sid="AssumeBaselineWriter",
                    actions=["sts:AssumeRole"],
                    resources=[props.baseline_writer_role_arn],
                ),
            )
            kms_key.grant_decrypt(task_role)
            task_role.add_to_principal_policy(
                iam.PolicyStatement(
                    actions=["cloudwatch:PutMetricData"],
                    resources=["*"],
                    conditions={"StringEquals": {"cloudwatch:namespace": "mmc/analyser/v1"}},
                ),
            )

            task_def = ecs.FargateTaskDefinition(
                self,
                f"TaskDef{analyser.title()}",
                cpu=props.fargate_cpu,
                memory_limit_mib=props.fargate_memory_mib,
                task_role=task_role,  # ty: ignore[invalid-argument-type]
                family=f"mmc-{env}-baseline-{analyser}",
            )
            task_def.add_container(
                f"Container{analyser.title()}",
                image=ecs.ContainerImage.from_registry(props.analyser_image_uris[analyser]),
                logging=ecs.LogDriver.aws_logs(
                    stream_prefix=f"baseline-{analyser}",
                    log_group=log_groups[analyser],
                ),
                environment={
                    "PROJECT_NAME": props.project_name,
                    "ANALYSER_TYPE": f"baseline-{analyser}",
                    "ENVIRONMENT": env,
                    "VARIANT": "Baseline",
                    "BASELINE_WRITER_ROLE_ARN": props.baseline_writer_role_arn,
                    "BASELINES_BUCKET_ARN": props.baselines_bucket_arn,
                    "MMC_ANALYSER_MODULE": _ANALYSER_MODULES[analyser],
                },
            )
            defs[analyser] = task_def
        return defs

    def _build_state_machine(
        self,
        *,
        env: str,
        task_defs: dict[str, ecs.FargateTaskDefinition],
        cluster: ecs.ICluster,
    ) -> sfn.StateMachine:
        """SFN Standard with v2 Lambda-first flow.

        Flow: LoadAndGate → ApprovalCheck → Parallel → EvaluateResults → WriteDecision
        → WriteRegistry → SuccessEnd (or terminal rejection states).

        1. LoadAndGate: invoke Lambda to load manifest/config and gate baseline
        2. ApprovalCheck: Choice state — if rejected, route to RejectEnd; else continue
        3. AllAnalysers: Parallel with 5 ECS analyser branches (mq, dq, bias, explain, shadow)
        4. EvaluateResults: invoke Lambda to check all required analysers succeeded
        5. WriteDecision: Choice state — if all_required_ok, route to WriteRegistry; else RejectEnd
        6. WriteRegistry: invoke Lambda to record baseline approval in DynamoDB
        7. RejectEnd / SuccessEnd: terminal states

        Returns:
            The created state machine.
        """
        props = self._props

        # Build the 5 ECS analyser branches for the Parallel state
        branches: list[dict[str, Any]] = [
            self._branch_definition(analyser=analyser, task_def=task_def, cluster=cluster)
            for analyser, task_def in task_defs.items()
        ]
        parallel_state = {
            "Type": "Parallel",
            "Branches": branches,
            "ResultPath": "$.analyser_results",
            "Next": "EvaluateResults",
        }

        # LoadAndGate: load manifest + config, gate baseline execution
        load_and_gate_state = {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
                "FunctionName": self.load_and_gate_fn.function_arn,
                "Payload": {
                    "manifest_uri.$": "$.manifest_uri",
                    "config_uri.$": "$.config_uri",
                    "project.$": "$.project",
                },
            },
            "ResultPath": "$.gate",
            "Retry": [
                {
                    "ErrorEquals": [
                        "Lambda.ServiceException",
                        "Lambda.AWSLambdaException",
                        "Lambda.SdkClientException",
                        "States.TaskFailed",
                    ],
                    "IntervalSeconds": props.sfn_retry_backoff_seconds,
                    "MaxAttempts": props.sfn_retry_max_attempts,
                    "BackoffRate": 2.0,
                }
            ],
            "Catch": [
                {
                    "ErrorEquals": ["States.ALL"],
                    "Next": "FailState",
                }
            ],
            "Next": "ApprovalCheck",
        }

        # ApprovalCheck: Choice state — check $.gate.status for rejection
        approval_check_state = {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.gate.status",
                    "StringEquals": "rejected",
                    "Next": "RejectEnd",
                }
            ],
            "Default": "AllAnalysers",
        }

        # EvaluateResults: check that required analysers succeeded
        evaluate_results_state = {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
                "FunctionName": self.evaluate_results_fn.function_arn,
                "Payload": {
                    "gate_output.$": "$.gate",
                    "analyser_results.$": "$.analyser_results",
                    "required_analysers": list(_ANALYSER_TYPES),
                },
            },
            "ResultPath": "$.eval_output",
            "Retry": [
                {
                    "ErrorEquals": [
                        "Lambda.ServiceException",
                        "Lambda.AWSLambdaException",
                        "Lambda.SdkClientException",
                        "States.TaskFailed",
                    ],
                    "IntervalSeconds": props.sfn_retry_backoff_seconds,
                    "MaxAttempts": props.sfn_retry_max_attempts,
                    "BackoffRate": 2.0,
                }
            ],
            "Catch": [
                {
                    "ErrorEquals": ["States.ALL"],
                    "Next": "FailState",
                }
            ],
            "Next": "WriteDecision",
        }

        # WriteDecision: Choice state — if all_required_ok, continue to WriteRegistry; else reject
        write_decision_state = {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.eval_output.all_required_ok",
                    "BooleanEquals": True,
                    "Next": "WriteRegistry",
                }
            ],
            "Default": "RejectEnd",
        }

        # WriteRegistry: record baseline approval in DynamoDB
        write_registry_state = {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
                "FunctionName": self.write_registry_fn.function_arn,
                "Payload": {
                    "project.$": "$.project",
                    "model_version.$": "$.model_version",
                    "status": "approved",
                    "baseline_prefix.$": "$.baseline_prefix",
                    "analysers.$": "$.analyser_results",
                    "manifest_uri.$": "$.manifest_uri",
                    "sfn_execution_arn.$": "$$.Execution.Arn",
                },
            },
            "ResultPath": "$.write_output",
            "Retry": [
                {
                    "ErrorEquals": [
                        "Lambda.ServiceException",
                        "Lambda.AWSLambdaException",
                        "Lambda.SdkClientException",
                        "States.TaskFailed",
                    ],
                    "IntervalSeconds": props.sfn_retry_backoff_seconds,
                    "MaxAttempts": props.sfn_retry_max_attempts,
                    "BackoffRate": 2.0,
                }
            ],
            "Catch": [
                {
                    "ErrorEquals": ["States.ALL"],
                    "Next": "FailState",
                }
            ],
            "Next": "SuccessEnd",
        }

        # Terminal states
        reject_end_state = {
            "Type": "Fail",
            "Error": "BaselineRejected",
            "Cause": "Baseline did not meet approval criteria",
        }

        success_end_state = {"Type": "Succeed"}

        fail_state = {
            "Type": "Fail",
            "Error": "BaselineExecutionFailed",
            "Cause": "A critical baseline process failed",
        }

        definition = {
            "Comment": f"mmc-{env}-{props.project_name}-baseline",
            "StartAt": "LoadAndGate",
            "States": {
                "LoadAndGate": load_and_gate_state,
                "ApprovalCheck": approval_check_state,
                "AllAnalysers": parallel_state,
                "EvaluateResults": evaluate_results_state,
                "WriteDecision": write_decision_state,
                "WriteRegistry": write_registry_state,
                "RejectEnd": reject_end_state,
                "SuccessEnd": success_end_state,
                "FailState": fail_state,
            },
        }

        return sfn.StateMachine(
            self,
            "BaselineStateMachine",
            state_machine_name=f"mmc-{env}-{props.project_name}-baseline",
            state_machine_type=sfn.StateMachineType.STANDARD,
            definition_body=sfn.DefinitionBody.from_string(json.dumps(definition)),
            timeout=Duration.hours(6),
        )

    def _branch_definition(
        self,
        *,
        analyser: str,
        task_def: ecs.FargateTaskDefinition,
        cluster: ecs.ICluster,
    ) -> dict[str, Any]:
        """One Parallel branch — RunTask with Retry + Catch → per-branch Pass.

        Returns:
            Amazon States Language branch dict.
        """
        props = self._props
        subnet_ids = [subnet.subnet_id for subnet in cluster.vpc.private_subnets or cluster.vpc.public_subnets]
        container_name = f"Container{analyser.title()}"
        run_task_state = {
            "Type": "Task",
            "Resource": "arn:aws:states:::ecs:runTask.sync",
            "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": cluster.cluster_arn,
                "TaskDefinition": task_def.task_definition_arn,
                "NetworkConfiguration": {
                    "AwsvpcConfiguration": {
                        "Subnets": subnet_ids,
                        "AssignPublicIp": "DISABLED",
                    },
                },
                "Overrides": {
                    "ContainerOverrides": [
                        {
                            "Name": container_name,
                            "Environment": [
                                {"Name": "PROJECT_NAME", "Value": props.project_name},
                                {"Name": "RUN_ID", "Value.$": "$.run_id"},
                                {"Name": "ANALYSER_TYPE", "Value": f"baseline-{analyser}"},
                                {"Name": "SNAPSHOT_URI", "Value.$": "$.snapshot_uri"},
                                {"Name": "ENVIRONMENT", "Value": props.environment},
                                {"Name": "MMC_ANALYSER_MODULE", "Value": _ANALYSER_MODULES[analyser]},
                            ],
                        },
                    ],
                },
            },
            "Retry": [
                {
                    "ErrorEquals": ["States.TaskFailed", "ECS.AmazonECSException"],
                    "IntervalSeconds": props.sfn_retry_backoff_seconds,
                    "MaxAttempts": props.sfn_retry_max_attempts,
                    "BackoffRate": 2.0,
                },
            ],
            "Catch": [
                {
                    "ErrorEquals": ["States.ALL"],
                    "Next": f"Branch{analyser.title()}Failed",
                    "ResultPath": "$.error",
                },
            ],
            "End": True,
        }
        failed_state = {
            "Type": "Pass",
            "End": True,
            "Result": {"analyser": f"baseline-{analyser}", "outcome": "branch_failed"},
        }
        return {
            "StartAt": f"Run{analyser.title()}",
            "States": {
                f"Run{analyser.title()}": run_task_state,
                f"Branch{analyser.title()}Failed": failed_state,
            },
        }

    def _maybe_grant_cross_account_put_events(self, *, env: str) -> None:
        """Attach a bus policy allowing PutEvents from the producer account.

        No-op unless ``producer_account_id`` is set and differs from
        ``operations_account_id``. See ADR 010: the producer-account
        :class:`ProducerEventsStack` forwards S3 events to this account's
        default bus; that PutEvents call requires an explicit bus policy on
        the target side.

        Args:
            env: Deployment environment tag used in the statement id.
        """
        producer = self._props.producer_account_id
        if producer is None or producer == self._props.operations_account_id:
            return
        default_bus = events.EventBus.from_event_bus_name(self, "DefaultEventBus", "default")
        events.CfnEventBusPolicy(
            self,
            "AllowCrossAccountPutEvents",
            event_bus_name=default_bus.event_bus_name,
            statement_id=f"mmc-{env}-{self._props.project_name}-allow-producer",
            statement={
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{producer}:root"},
                "Action": "events:PutEvents",
                "Resource": default_bus.event_bus_arn,
            },
        )

    def _build_event_rule(self, *, env: str, state_machine: sfn.StateMachine) -> events.Rule:
        """S3 ``Object Created`` rule → SFN ``StartExecution``.

        Matches events on the local default bus. In a cross-account topology
        the same pattern matches events forwarded here by
        :class:`~model_monitor_cdk.stacks.producer_events_stack.ProducerEventsStack`
        (ADR 010); the bus policy granting ``events:PutEvents`` is added by
        :meth:`_maybe_grant_cross_account_put_events`.

        Returns:
            The created EventBridge rule.
        """
        props = self._props
        producer_bucket_name = _bucket_name_from_arn(props.producer_bucket_arn)
        rule = events.Rule(
            self,
            "SnapshotObjectCreatedRule",
            rule_name=f"mmc-{env}-{props.project_name}-baseline-trigger",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [producer_bucket_name]},
                    "object": {"key": [{"prefix": props.producer_prefix}]},
                },
            ),
        )
        rule.add_target(
            events_targets.SfnStateMachine(  # ty: ignore[invalid-argument-type]
                state_machine,
                input=events.RuleTargetInput.from_object(
                    {
                        "snapshot_uri": events.EventField.from_path("$.detail.object.key"),
                        "run_id": events.EventField.from_path("$.id"),
                    },
                ),
            ),
        )
        return rule

    def _build_baseline_lambdas(
        self,
        *,
        env: str,
        baselines_bucket: s3.IBucket,
        kms_key: kms.IKey,
    ) -> None:
        """Provision three baseline control Lambdas: LoadAndGate, EvaluateResults, WriteRegistry.

        Args:
            env: Deployment environment tag.
            baselines_bucket: Cross-account baselines S3 bucket.
            kms_key: Cross-account KMS key for S3 encryption.
        """
        cdk_src_dir = Path(__file__).resolve().parent.parent

        # LoadAndGateFn: fetch manifest + config, gate baseline execution
        self.load_and_gate_fn = lambda_.Function(
            self,
            "LoadAndGateFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="model_monitor_cdk.load_and_gate.load_and_gate",
            code=lambda_.Code.from_asset(str(cdk_src_dir)),
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={"ENVIRONMENT": env},
            description="Load manifest + config, gate baseline execution",
        )
        # LoadAndGate reads from S3 URIs passed as input; runtime IAM grants via SFN task role
        # (no direct bucket access needed at stack provisioning time)

        # EvaluateResultsFn: check that required analysers succeeded
        self.evaluate_results_fn = lambda_.Function(
            self,
            "EvaluateResultsFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="model_monitor_cdk.evaluate_results.evaluate_results",
            code=lambda_.Code.from_asset(str(cdk_src_dir)),
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={"ENVIRONMENT": env},
            description="Evaluate analyser results and gate progression",
        )

        # WriteRegistryFn: record baseline approval in DynamoDB
        self.write_registry_fn = lambda_.Function(
            self,
            "WriteRegistryFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="model_monitor_cdk.write_registry.write_registry",
            code=lambda_.Code.from_asset(str(cdk_src_dir)),
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                "ENVIRONMENT": env,
                "BASELINE_REGISTRY_TABLE_NAME": self.registry_table.table_name,
            },
            description="Write baseline approval record to registry table",
        )
        self.registry_table.grant_write_data(self.write_registry_fn)
