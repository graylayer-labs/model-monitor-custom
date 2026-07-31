"""InferenceMonitorStack — live analyser runtime per ml-inference-* account.

See ADR ``001-iac-layout`` (cross-account posture, naming) and ADR
``006-observability-contract`` (DDB table shape, CW namespace, log group
naming) plus ADR ``007-failure-taxonomy`` (Pipes filter rationale).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal

from model_monitor_cdk.config import ComputeBackend

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
    aws_ecr as ecr,
)
from aws_cdk import (
    aws_ecs as ecs,
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
    aws_pipes as pipes,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_scheduler as scheduler,
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

_LAMBDA_HANDLERS_DIR = Path(__file__).resolve().parent.parent / "lambda_handlers"


@dataclass(frozen=True, kw_only=True)
class InferenceMonitorStackProps:
    """Configuration for :class:`InferenceMonitorStack`.

    Attributes:
        environment: Deployment environment tag (``test``/``prod``/``dev``).
        project_name: Model / silo id used in naming and tags.
        consumer_account_id: 12-digit id of the ml-inference-* account.
        artifact_account_id: 12-digit id of the ml-artifact account.
        artifact_kms_key_arn: Cross-account KMS key ARN.
        baselines_bucket_arn: Cross-account baselines bucket ARN.
        analyser_image_uris: ECR URIs keyed by analyser type
            (``mq``/``dq``/``bias``/``explain``/``shadow``).
        schedule_expression: EventBridge Scheduler cron for the tick.
        compute_backend: Compute backend (``"lambda"`` or ``"ecs"``).
        enable_event_wiring: When ``False``, skip EventBridge Scheduler and Pipes
            creation (useful for LocalStack testing, which doesn't support Pro-tier
            features). Default ``True`` for real AWS.
        fargate_cpu: vCPU units for each analyser task (ECS only).
        fargate_memory_mib: Memory (MiB) for each analyser task (ECS only).
        lambda_memory_mib: Memory (MiB) for each analyser Lambda (Lambda only).
        lambda_timeout_seconds: Timeout in seconds for each analyser Lambda (Lambda only).
        sfn_retry_max_attempts: Max retries per Parallel branch.
        sfn_retry_backoff_seconds: IntervalSeconds seed for exponential backoff.
        analyser_image_source: Optional callable to override ECR image source
            (used in tests to build local Docker assets instead).
    """

    _REQUIRED_ANALYSERS: ClassVar[frozenset[str]] = frozenset(_ANALYSER_TYPES)

    environment: Literal["test", "prod", "dev"]
    project_name: str
    consumer_account_id: str
    artifact_account_id: str
    artifact_kms_key_arn: str
    baselines_bucket_arn: str
    analyser_image_uris: dict[str, str] = field(default_factory=dict)
    vpc_id: str | None = None
    schedule_expression: str = "cron(0 * * * ? *)"
    compute_backend: ComputeBackend = "lambda"
    enable_event_wiring: bool = True
    fargate_cpu: int = 1024
    fargate_memory_mib: int = 4096
    lambda_memory_mib: int = 3008
    lambda_timeout_seconds: int = 300
    sfn_retry_max_attempts: int = 3
    sfn_retry_backoff_seconds: int = 30
    analyser_image_source: Callable[[str], lambda_.DockerImageCode] | None = None

    def __post_init__(self) -> None:
        """Validate props at construction."""
        self._validate_identifiers()
        self._validate_images()
        self._validate_numeric()

    def _validate_identifiers(self) -> None:
        """Check names, account IDs, and cross-account ARNs.

        Raises:
            ValueError: If any identifier is empty or malformed.
        """
        if not self.project_name:
            msg = "project_name must be a non-empty string"
            raise ValueError(msg)
        if not _ACCOUNT_ID_PATTERN.match(self.consumer_account_id):
            msg = f"consumer_account_id must be 12 digits, got: {self.consumer_account_id!r}"
            raise ValueError(msg)
        if not _ACCOUNT_ID_PATTERN.match(self.artifact_account_id):
            msg = f"artifact_account_id must be 12 digits, got: {self.artifact_account_id!r}"
            raise ValueError(msg)
        if not self.artifact_kms_key_arn:
            msg = "artifact_kms_key_arn must be non-empty"
            raise ValueError(msg)
        if not self.baselines_bucket_arn:
            msg = "baselines_bucket_arn must be non-empty"
            raise ValueError(msg)

    def _validate_images(self) -> None:
        """Check that every analyser has a well-formed ECR URI.

        Raises:
            ValueError: If keys are missing/extra or a URI isn't an ECR URI.
        """
        # Skip validation if using a custom image source hook (e.g., for LocalStack tests)
        if self.analyser_image_source is not None:
            return

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
        if self.lambda_memory_mib <= 0:
            msg = "lambda_memory_mib must be positive"
            raise ValueError(msg)
        if self.lambda_timeout_seconds <= 0:
            msg = "lambda_timeout_seconds must be positive"
            raise ValueError(msg)
        if self.sfn_retry_max_attempts < 0:
            msg = "sfn_retry_max_attempts must be non-negative"
            raise ValueError(msg)
        if self.sfn_retry_backoff_seconds <= 0:
            msg = "sfn_retry_backoff_seconds must be positive"
            raise ValueError(msg)


class InferenceMonitorStack(Stack):
    """Live analyser runtime stack, one instance per ``ml-inference-*`` account."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: InferenceMonitorStackProps,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        """Wire the live analyser runtime.

        Args:
            scope: Parent construct.
            construct_id: CDK construct id.
            props: Validated configuration.
            **kwargs: Passed through to :class:`aws_cdk.Stack`.
        """
        super().__init__(scope, construct_id, **kwargs)
        self._props = props

        Tags.of(self).add("Component", "monitor")
        Tags.of(self).add("Project", props.project_name)
        Tags.of(self).add("Environment", props.environment)

        env = props.environment

        kms_key = kms.Key.from_key_arn(self, "ArtifactKmsKey", key_arn=props.artifact_kms_key_arn)
        baselines_bucket = s3.Bucket.from_bucket_arn(
            self,
            "BaselinesBucket",
            bucket_arn=props.baselines_bucket_arn,
        )

        outcomes_table = dynamodb.Table(
            self,
            "OutcomesTable",
            table_name=f"mmc-{env}-outcomes",
            partition_key=dynamodb.Attribute(name="run_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="analyser_type", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            stream=dynamodb.StreamViewType.NEW_IMAGE,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=kms_key,
            removal_policy=RemovalPolicy.RETAIN,
        )

        archive_bucket = s3.Bucket(
            self,
            "OutcomesArchiveBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=kms_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        log_groups = self._build_log_groups(env)

        if props.compute_backend == "ecs":
            vpc: ec2.IVpc = (
                ec2.Vpc.from_lookup(self, "AnalyserVpc", vpc_id=props.vpc_id)
                if props.vpc_id
                else ec2.Vpc(self, "AnalyserVpc", max_azs=2, nat_gateways=0)
            )
            cluster = ecs.Cluster(
                self,
                "AnalyserCluster",
                cluster_name=f"mmc-{env}-inference",
                vpc=vpc,
                enable_fargate_capacity_providers=True,
            )
            task_defs = self._build_task_definitions(
                env=env,
                outcomes_table=outcomes_table,
                baselines_bucket=baselines_bucket,
                kms_key=kms_key,
                log_groups=log_groups,
            )
            state_machine = self._build_state_machine_ecs(
                env=env,
                task_defs=task_defs,
                cluster=cluster,
            )
        else:  # lambda
            lambda_functions = self._build_lambda_functions(
                env=env,
                outcomes_table=outcomes_table,
                baselines_bucket=baselines_bucket,
                kms_key=kms_key,
                log_groups=log_groups,
            )
            state_machine = self._build_state_machine_lambda(
                env=env,
                lambda_functions=lambda_functions,
            )

        # EventBridge Scheduler and Pipes are Pro-tier only in LocalStack.
        # Gate them behind enable_event_wiring for local testing.
        if props.enable_event_wiring:
            self._build_scheduler(env=env, state_machine=state_machine)

            notifier_fn, notifier_pipe = self._build_notifier_pipe(
                env=env,
                outcomes_table=outcomes_table,
            )
            _archive_fn, archive_pipe = self._build_archive_pipe(
                env=env,
                outcomes_table=outcomes_table,
                archive_bucket=archive_bucket,
            )

            CfnOutput(self, "NotifierLambdaArn", value=notifier_fn.function_arn)
            CfnOutput(self, "NotifierPipeName", value=notifier_pipe.ref)
            CfnOutput(self, "ArchivePipeName", value=archive_pipe.ref)

        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
        CfnOutput(self, "OutcomesTableName", value=outcomes_table.table_name)
        CfnOutput(self, "OutcomesStreamArn", value=outcomes_table.table_stream_arn or "")
        CfnOutput(self, "ArchiveBucketName", value=archive_bucket.bucket_name)

    def _build_log_groups(self, env: str) -> dict[str, logs.LogGroup]:
        """Create ``/mmc/<env>/<analyser>`` log groups per ADR 006.

        Returns:
            Log group keyed by analyser type.
        """
        return {
            analyser: logs.LogGroup(
                self,
                f"LogGroup{analyser.title()}",
                log_group_name=f"/mmc/{env}/{analyser}",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            )
            for analyser in _ANALYSER_TYPES
        }

    def _build_lambda_functions(
        self,
        *,
        env: str,
        outcomes_table: dynamodb.ITable,
        baselines_bucket: s3.IBucket,
        kms_key: kms.IKey,
        log_groups: dict[str, logs.LogGroup],
    ) -> dict[str, lambda_.DockerImageFunction]:
        """One Lambda function per analyser, with a least-privilege execution role.

        Returns:
            Lambda function keyed by analyser type.
        """
        props = self._props
        fns: dict[str, lambda_.DockerImageFunction] = {}
        for analyser in _ANALYSER_TYPES:
            # Create execution role for Lambda.
            exec_role = iam.Role(
                self,
                f"LambdaExecRole{analyser.title()}",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),  # ty: ignore[invalid-argument-type]
                description=f"Lambda execution role for mmc {analyser} analyser",
            )
            baselines_bucket.grant_read(exec_role)
            baselines_bucket.grant_put(exec_role)
            outcomes_table.grant(exec_role, "dynamodb:PutItem", "dynamodb:UpdateItem")
            kms_key.grant_decrypt(exec_role)
            exec_role.add_to_principal_policy(
                iam.PolicyStatement(
                    actions=["cloudwatch:PutMetricData"],
                    resources=["*"],
                    conditions={"StringEquals": {"cloudwatch:namespace": "mmc/analyser/v1"}},
                ),
            )

            # Get the image source (default: ECR, override in tests for local builds).
            if props.analyser_image_source is not None:
                image_code = props.analyser_image_source(analyser)
            else:
                # Parse ECR URI and build image code from ECR.
                uri = props.analyser_image_uris[analyser]
                # URI format: <account>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>
                parts = uri.split("/", 1)
                host = parts[0]
                repo_and_tag = parts[1]
                host_parts = host.split(".")
                region = host_parts[3]
                repo_name = repo_and_tag.split(":")[0]
                tag = repo_and_tag.split(":", 1)[1] if ":" in repo_and_tag else "latest"

                artifact_account = props.artifact_account_id
                repo = ecr.Repository.from_repository_arn(
                    self,
                    f"EcrRepo{analyser.title()}",
                    f"arn:aws:ecr:{region}:{artifact_account}:repository/{repo_name}",
                )
                image_code = lambda_.DockerImageCode.from_ecr(repository=repo, tag_or_digest=tag)

            fn = lambda_.DockerImageFunction(
                self,
                f"Analyser{analyser.title()}",
                code=image_code,
                role=exec_role,  # ty: ignore[invalid-argument-type]
                function_name=f"mmc-{env}-{analyser}",
                memory_size=props.lambda_memory_mib,
                timeout=Duration.seconds(props.lambda_timeout_seconds),
                environment={
                    "OUTCOMES_TABLE_NAME": outcomes_table.table_name,
                    "ENVIRONMENT": env,
                    "MMC_ANALYSER_MODULE": _ANALYSER_MODULES[analyser],
                },
                log_retention=logs.RetentionDays.TWO_WEEKS,
            )
            fns[analyser] = fn
        return fns

    def _build_task_definitions(
        self,
        *,
        env: str,
        outcomes_table: dynamodb.ITable,
        baselines_bucket: s3.IBucket,
        kms_key: kms.IKey,
        log_groups: dict[str, logs.LogGroup],
    ) -> dict[str, ecs.FargateTaskDefinition]:
        """One Fargate task definition per analyser, with a least-privilege task role.

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
                description=f"Fargate task role for mmc {analyser} analyser",
            )
            baselines_bucket.grant_read(task_role)
            baselines_bucket.grant_put(task_role)
            outcomes_table.grant(task_role, "dynamodb:PutItem", "dynamodb:UpdateItem")
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
                family=f"mmc-{env}-{analyser}",
            )
            task_def.add_container(
                f"Container{analyser.title()}",
                image=ecs.ContainerImage.from_registry(props.analyser_image_uris[analyser]),
                logging=ecs.LogDriver.aws_logs(stream_prefix=analyser, log_group=log_groups[analyser]),
                environment={
                    "PROJECT_NAME": props.project_name,
                    "ANALYSER_TYPE": analyser,
                    "ENVIRONMENT": env,
                    "VARIANT": "AllTraffic",
                    "OUTCOMES_TABLE_NAME": outcomes_table.table_name,
                    "MMC_ANALYSER_MODULE": _ANALYSER_MODULES[analyser],
                },
            )
            defs[analyser] = task_def
        return defs

    def _build_state_machine_ecs(
        self,
        *,
        env: str,
        task_defs: dict[str, ecs.FargateTaskDefinition],
        cluster: ecs.ICluster,
    ) -> sfn.StateMachine:
        """SFN Standard with a Parallel of five per-analyser branches.

        Each branch owns its own Retry + Catch so a single-analyser failure
        never fails the whole run. Written via a raw Chain built from a
        Pass state — the Parallel is expressed as a JSONPath definition
        substitution so branch shape stays inspectable in tests.

        Returns:
            The created state machine.
        """
        props = self._props
        branches: list[dict[str, Any]] = []
        for analyser, task_def in task_defs.items():
            branches.append(self._branch_definition(analyser=analyser, task_def=task_def, cluster=cluster))

        parallel_state = {
            "Type": "Parallel",
            "End": True,
            "Branches": branches,
        }
        definition = {
            "Comment": f"mmc-{env}-{props.project_name}-inference",
            "StartAt": "AllAnalysers",
            "States": {"AllAnalysers": parallel_state},
        }
        return sfn.StateMachine(
            self,
            "InferenceStateMachine",
            state_machine_name=f"mmc-{env}-{props.project_name}-inference",
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
                                {"Name": "PROJECT_NAME", "Value.$": "$.project"},
                                {"Name": "RUN_ID", "Value.$": "$.run_id"},
                                {"Name": "ANALYSER_TYPE", "Value": analyser},
                                {"Name": "INPUT_URIS_JSON", "Value.$": "$.input_uris_json"},
                                {"Name": "OUTPUT_URI", "Value.$": "$.output_uri"},
                                {"Name": "CONFIG_URI", "Value.$": "$.config_uri"},
                                {"Name": "ENVIRONMENT", "Value": props.environment},
                                {"Name": "VARIANT", "Value.$": "$.variant"},
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
            "Result": {"analyser": analyser, "outcome": "branch_failed"},
        }
        return {
            "StartAt": f"Run{analyser.title()}",
            "States": {
                f"Run{analyser.title()}": run_task_state,
                f"Branch{analyser.title()}Failed": failed_state,
            },
        }

    def _build_state_machine_lambda(
        self,
        *,
        env: str,
        lambda_functions: dict[str, lambda_.DockerImageFunction],
    ) -> sfn.StateMachine:
        """SFN Standard with a Parallel of five per-analyser Lambda invoke branches.

        Each branch owns its own Retry + Catch so a single-analyser failure
        never fails the whole run. Written via a raw Chain built from a
        Pass state — the Parallel is expressed as a JSONPath definition
        substitution so branch shape stays inspectable in tests.

        Returns:
            The created state machine.
        """
        props = self._props
        branches: list[dict[str, Any]] = []
        for analyser in _ANALYSER_TYPES:
            branch = self._lambda_branch_definition(
                analyser=analyser,
                fn=lambda_functions[analyser],
                env=env,
            )
            branches.append(branch)

        parallel_state = {
            "Type": "Parallel",
            "End": True,
            "Branches": branches,
            "Retry": [
                {
                    "ErrorEquals": ["States.ALL"],
                    "IntervalSeconds": props.sfn_retry_backoff_seconds,
                    "MaxAttempts": props.sfn_retry_max_attempts,
                    "BackoffRate": 2.0,
                }
            ],
        }

        definition_obj = {"Comment": f"mmc-{env} 5-way parallel analyser runner (Lambda backend)", "StartAt": "AllAnalysers", "States": {"AllAnalysers": parallel_state}}
        definition_text = json.dumps(definition_obj)

        state_machine = sfn.StateMachine(
            self,
            "AnalyserStateMachine",
            state_machine_name=f"mmc-{env}-inference-analysers",
            definition_body=sfn.ChainDefinitionBody.from_string(definition_text),
        )
        return state_machine

    def _lambda_branch_definition(
        self,
        *,
        analyser: str,
        fn: lambda_.DockerImageFunction,
        env: str,
    ) -> dict[str, Any]:
        """ASL definition for a single Lambda-invoke branch.

        Args:
            analyser: Analyser type (mq/dq/bias/explain/shadow).
            fn: The Lambda function to invoke.
            env: Environment tag.

        Returns:
            A dict representing the branch (StartAt, States dict).
        """
        invoke_state = {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
                "FunctionName": fn.function_arn,
                "Payload": {
                    "project.$": "$.project",
                    "run_id.$": "$.run_id",
                    "analyser_type": analyser,
                    "input_uris_json.$": "$.input_uris_json",
                    "output_uri.$": "$.output_uri",
                    "config_uri.$": "$.config_uri",
                    "environment": env,
                    "variant.$": "$.variant",
                },
            },
            "ResultSelector": {"result.$": "$.Payload"},
            "Retry": [
                {
                    "ErrorEquals": [
                        "Lambda.ServiceException",
                        "Lambda.AWSLambdaException",
                        "Lambda.SdkClientException",
                        "States.TaskFailed",
                    ],
                    "IntervalSeconds": self._props.sfn_retry_backoff_seconds,
                    "MaxAttempts": self._props.sfn_retry_max_attempts,
                    "BackoffRate": 2.0,
                }
            ],
            "Catch": [
                {
                    "ErrorEquals": ["States.ALL"],
                    "Next": f"Branch{analyser.title()}Failed",
                    "ResultPath": "$.error",
                }
            ],
            "Next": f"Branch{analyser.title()}Succeeded",
        }
        succeeded_state = {"Type": "Pass", "End": True}
        failed_state = {
            "Type": "Pass",
            "Result": {"analyser": analyser, "outcome": "branch_failed"},
            "ResultPath": "$.branch_failure",
            "End": True,
        }

        return {
            "StartAt": f"Run{analyser.title()}",
            "States": {
                f"Run{analyser.title()}": invoke_state,
                f"Branch{analyser.title()}Succeeded": succeeded_state,
                f"Branch{analyser.title()}Failed": failed_state,
            },
        }

    def _build_scheduler(self, *, env: str, state_machine: sfn.StateMachine) -> None:
        """EventBridge Scheduler that ticks the state machine on cron."""
        props = self._props
        sched_role = iam.Role(
            self,
            "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),  # ty: ignore[invalid-argument-type]
            description="Role assumed by EventBridge Scheduler to start SFN executions",
        )
        state_machine.grant_start_execution(sched_role)
        payload = {
            "run_id": "<aws.scheduler.execution-id>",
            "project": props.project_name,
            "environment": env,
            "variant": "AllTraffic",
            "input_uris_json": "[]",
            "output_uri": "s3://placeholder/output",
            "config_uri": "s3://placeholder/config.json",
        }
        scheduler.CfnSchedule(
            self,
            "InferenceTick",
            name=f"mmc-{env}-{props.project_name}-tick",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression=props.schedule_expression,
            target=scheduler.CfnSchedule.TargetProperty(
                arn=state_machine.state_machine_arn,
                role_arn=sched_role.role_arn,
                input=json.dumps(payload),
            ),
        )

    def _build_notifier_pipe(
        self,
        *,
        env: str,
        outcomes_table: dynamodb.ITable,
    ) -> tuple[lambda_.Function, pipes.CfnPipe]:
        """Alert path: DDB stream → severity==alert filter → notifier Lambda.

        Returns:
            The notifier Lambda function and the pipe wiring the stream to it.
        """
        notifier_fn = lambda_.Function(
            self,
            "NotifierFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(_LAMBDA_HANDLERS_DIR / "notifier")),
            environment={"OUTCOMES_TABLE_NAME": outcomes_table.table_name},
            timeout=Duration.seconds(30),
            description="Logs a NOTIFY line per alert-severity outcome (idempotent)",
        )
        outcomes_table.grant_write_data(notifier_fn)

        pipe_role = self._make_pipe_role("NotifierPipeRole", outcomes_table, notifier_fn)
        pattern = {"dynamodb": {"NewImage": {"severity": {"S": ["alert"]}}}}
        pipe = pipes.CfnPipe(
            self,
            "NotifierPipe",
            name=f"mmc-{env}-outcome-notifier",
            role_arn=pipe_role.role_arn,
            source=outcomes_table.table_stream_arn or "",
            target=notifier_fn.function_arn,
            source_parameters=pipes.CfnPipe.PipeSourceParametersProperty(
                dynamo_db_stream_parameters=pipes.CfnPipe.PipeSourceDynamoDBStreamParametersProperty(
                    starting_position="LATEST",
                    batch_size=1,
                ),
                filter_criteria=pipes.CfnPipe.FilterCriteriaProperty(
                    filters=[pipes.CfnPipe.FilterProperty(pattern=json.dumps(pattern))],
                ),
            ),
        )
        return notifier_fn, pipe

    def _build_archive_pipe(
        self,
        *,
        env: str,
        outcomes_table: dynamodb.ITable,
        archive_bucket: s3.IBucket,
    ) -> tuple[lambda_.Function, pipes.CfnPipe]:
        """Archive path: DDB stream → no filter → archive Lambda → S3.

        Returns:
            The archive Lambda function and the pipe wiring the stream to it.
        """
        archive_fn = lambda_.Function(
            self,
            "ArchiveFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(_LAMBDA_HANDLERS_DIR / "archive_writer")),
            environment={"ARCHIVE_BUCKET": archive_bucket.bucket_name},
            timeout=Duration.seconds(30),
            description="Writes every outcomes stream record to the archive bucket",
        )
        archive_bucket.grant_put(archive_fn)

        pipe_role = self._make_pipe_role("ArchivePipeRole", outcomes_table, archive_fn)
        pipe = pipes.CfnPipe(
            self,
            "ArchivePipe",
            name=f"mmc-{env}-outcome-archive",
            role_arn=pipe_role.role_arn,
            source=outcomes_table.table_stream_arn or "",
            target=archive_fn.function_arn,
            source_parameters=pipes.CfnPipe.PipeSourceParametersProperty(
                dynamo_db_stream_parameters=pipes.CfnPipe.PipeSourceDynamoDBStreamParametersProperty(
                    starting_position="LATEST",
                    batch_size=10,
                ),
            ),
        )
        return archive_fn, pipe

    def _make_pipe_role(
        self,
        role_id: str,
        outcomes_table: dynamodb.ITable,
        target_fn: lambda_.Function,
    ) -> iam.Role:
        """Least-privilege pipe role: read the DDB stream, invoke the target Lambda.

        Returns:
            The IAM role assumed by the EventBridge Pipe.
        """
        role = iam.Role(
            self,
            role_id,
            assumed_by=iam.ServicePrincipal("pipes.amazonaws.com"),  # ty: ignore[invalid-argument-type]
            description="Least-privilege role for an outcomes DDB stream pipe",
        )
        role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:DescribeStream",
                    "dynamodb:GetRecords",
                    "dynamodb:GetShardIterator",
                    "dynamodb:ListStreams",
                ],
                resources=[outcomes_table.table_stream_arn or "*"],
            ),
        )
        target_fn.grant_invoke(role)
        return role
