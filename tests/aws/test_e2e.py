"""End-to-end AWS tests for model-monitor-custom.

Tests complete workflows:
1. Baseline analysis: snapshot → 5 analysers → registry
2. Monitor analysis: predictions → 5 analysers → outcomes
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import boto3
import pytest

from tests.aws.fixtures.generate_test_data import generate_predictions_data, generate_training_data


@pytest.mark.aws
@pytest.mark.slow
class TestBaselineWorkflow:
    """Test snapshot analysis end-to-end."""

    def test_baseline_analysis_completes_successfully(
        self,
        s3_client,
        sfn_client,
        ddb_client,
        aws_config,
        aws_resource_names,
        test_data_paths,
    ):
        """Test baseline analysis workflow succeeds.

        Flow:
        1. Upload test manifest to S3
        2. Wait for Step Functions execution to complete
        3. Assert baseline registry entry created
        4. Assert all 5 analysers succeeded
        5. Assert S3 has analyser outputs
        """
        baselines_bucket = aws_resource_names["baselines_bucket"]
        project = aws_config["project"]
        model_version = aws_config["model_version"]

        # 1. Upload test data to S3
        print("\n[1/5] Uploading test data to S3...")
        training_data = generate_training_data()
        predictions_data = generate_predictions_data()

        # Convert to CSV (simpler for test, analysers handle both)
        training_csv = training_data.to_csv(index=False)
        predictions_csv = predictions_data.to_csv(index=False)

        s3_client.put_object(
            Bucket=baselines_bucket,
            Key=f"{project}/{model_version}/input/training.csv",
            Body=training_csv.encode(),
        )
        s3_client.put_object(
            Bucket=baselines_bucket,
            Key=f"{project}/{model_version}/input/predictions.csv",
            Body=predictions_csv.encode(),
        )

        # 2. Upload manifest (triggers baseline SFN)
        print("[2/5] Uploading manifest (triggers baseline analysis)...")
        with open(test_data_paths["manifest"]) as f:
            manifest = json.load(f)

        # Template substitution
        manifest_json = json.dumps(manifest).replace(
            "{{BASELINES_BUCKET}}", baselines_bucket
        )

        s3_client.put_object(
            Bucket=baselines_bucket,
            Key=f"{project}/{model_version}/manifest.json",
            Body=manifest_json,
        )

        # 3. Wait for Step Functions execution
        print("[3/5] Waiting for baseline SFN execution...")
        sfn_arn = self._get_baseline_sfn_arn(sfn_client, aws_config)
        execution_arn = self._wait_for_sfn_execution(
            sfn_client, sfn_arn, project, timeout_seconds=300
        )

        # 4. Assert baseline registry entry
        print("[4/5] Asserting baseline registry entry...")
        baseline_registry_table = aws_resource_names["baseline_registry_table"]
        registry_entry = self._get_baseline_registry_entry(
            ddb_client, baseline_registry_table, project, model_version
        )

        assert registry_entry is not None, "Baseline registry entry not found"
        assert (
            registry_entry["status"]["S"] == "approved"
        ), "Baseline status is not approved"

        # Check all 5 analysers present
        analysers = registry_entry.get("analysers", {}).get("M", {})
        expected_analysers = {"mq", "dq", "bias", "explain", "shadow"}
        actual_analysers = set(analysers.keys())

        assert (
            expected_analysers == actual_analysers
        ), f"Expected {expected_analysers}, got {actual_analysers}"

        # 5. Assert S3 has analyser outputs
        print("[5/5] Asserting analyser outputs in S3...")
        for analyser in expected_analysers:
            self._assert_s3_object_exists(
                s3_client,
                baselines_bucket,
                f"{project}/{model_version}/analysers/{analyser}/output.json",
            )

        print("✓ Baseline workflow completed successfully")

    def _get_baseline_sfn_arn(self, sfn_client, aws_config) -> str:
        """Get baseline Step Functions state machine ARN."""
        project = aws_config["project"].replace("_", "-")
        response = sfn_client.list_state_machines()
        for sm in response["stateMachines"]:
            if "baseline" in sm["name"].lower() and project in sm["name"].lower():
                return sm["stateMachineArn"]
        raise RuntimeError(f"Baseline SFN not found for project {project}")

    def _wait_for_sfn_execution(
        self, sfn_client, sfn_arn: str, project: str, timeout_seconds: int = 300
    ) -> str:
        """Wait for Step Functions execution to complete.

        Args:
            sfn_client: Boto3 Step Functions client
            sfn_arn: State machine ARN
            project: Project name (used in execution search)
            timeout_seconds: Max time to wait

        Returns:
            Execution ARN

        Raises:
            TimeoutError: If execution doesn't complete within timeout
        """
        start_time = time.time()
        poll_interval = 10  # seconds
        backoff = 1.1

        while time.time() - start_time < timeout_seconds:
            response = sfn_client.list_executions(
                stateMachineArn=sfn_arn,
                statusFilter="SUCCEEDED",
                maxItems=10,
            )

            for execution in response.get("executions", []):
                if project in execution["name"]:
                    return execution["executionArn"]

            # Poll again after interval
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * backoff, 30)  # Cap at 30s

        raise TimeoutError(f"SFN execution did not complete within {timeout_seconds}s")

    def _get_baseline_registry_entry(
        self, ddb_client, table_name: str, project: str, model_version: str
    ) -> dict | None:
        """Get baseline registry entry from DynamoDB."""
        response = ddb_client.get_item(
            TableName=table_name,
            Key={"project": {"S": project}, "sk": {"S": model_version}},
        )
        return response.get("Item")

    def _assert_s3_object_exists(
        self, s3_client, bucket: str, key: str
    ) -> None:
        """Assert object exists in S3."""
        try:
            s3_client.head_object(Bucket=bucket, Key=key)
        except s3_client.exceptions.NoSuchKey:
            raise AssertionError(f"S3 object not found: s3://{bucket}/{key}")


@pytest.mark.aws
@pytest.mark.slow
class TestMonitorWorkflow:
    """Test live monitoring analysis end-to-end."""

    def test_monitor_analysis_records_outcomes(
        self, ddb_client, lambda_client, aws_config, aws_resource_names
    ):
        """Test monitor analysis workflow records outcomes.

        Flow:
        1. Invoke monitor Lambda directly with test predictions
        2. Assert outcomes table populated
        3. Assert all 5 analyser results recorded
        4. Assert results have expected structure
        """
        outcomes_table = aws_resource_names["outcomes_table"]
        project = aws_config["project"]
        run_id = f"test-run-{int(time.time())}"

        # 1. Invoke monitor Lambda
        print(f"\n[1/3] Invoking monitor Lambda with test predictions (run_id={run_id})...")
        predictions_data = generate_predictions_data()
        payload = {
            "project": project,
            "run_id": run_id,
            "predictions_data": predictions_data.to_dict("records")[:10],  # First 10 rows
        }

        # Find monitor Lambda function
        monitor_fn_name = self._get_monitor_lambda_name(lambda_client, project)
        lambda_client.invoke(
            FunctionName=monitor_fn_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload),
        )

        # 2. Wait for outcomes to be written (async processing)
        print("[2/3] Waiting for outcomes to be recorded...")
        time.sleep(5)  # Give analysers time to write

        # 3. Assert outcomes recorded
        print("[3/3] Asserting outcomes in DynamoDB...")
        outcomes = self._get_outcomes(ddb_client, outcomes_table, run_id)

        assert len(outcomes) == 5, f"Expected 5 outcomes, got {len(outcomes)}"

        # Verify all analysers present
        analyser_types = {item["analyser_type"]["S"] for item in outcomes}
        expected_analysers = {"mq", "dq", "bias", "explain", "shadow"}
        assert (
            analyser_types == expected_analysers
        ), f"Expected {expected_analysers}, got {analyser_types}"

        # Verify each outcome has expected structure
        for outcome in outcomes:
            assert "run_id" in outcome
            assert "analyser_type" in outcome
            assert "outcome" in outcome
            assert outcome["outcome"]["S"] in ("success", "failure")

        print("✓ Monitor workflow completed successfully")

    def _get_monitor_lambda_name(self, lambda_client, project: str) -> str:
        """Get monitor Lambda function name."""
        response = lambda_client.list_functions()
        for fn in response["Functions"]:
            if "monitor" in fn["FunctionName"].lower() and project.lower() in fn[
                "FunctionName"
            ].lower():
                return fn["FunctionName"]
        raise RuntimeError(f"Monitor Lambda not found for project {project}")

    def _get_outcomes(
        self, ddb_client, table_name: str, run_id: str
    ) -> list[dict]:
        """Get all outcomes for a run from DynamoDB."""
        response = ddb_client.query(
            TableName=table_name,
            KeyConditionExpression="run_id = :run_id",
            ExpressionAttributeValues={":run_id": {"S": run_id}},
        )
        return response.get("Items", [])
