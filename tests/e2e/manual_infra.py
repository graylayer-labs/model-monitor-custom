"""Manually create infrastructure in LocalStack without CDK.

This avoids CDK's asset publishing issues and provides a direct, testable
infrastructure setup for E2E testing on LocalStack.
"""

from __future__ import annotations

import json
import boto3


def create_localstack_infrastructure() -> dict[str, str]:
    """Create baseline infrastructure in LocalStack.

    Returns:
        Dict with resource ARNs and names for testing.
    """
    # Use LocalStack endpoints
    s3 = boto3.client("s3", endpoint_url="http://localhost:4566", region_name="eu-west-1")
    ddb = boto3.client("dynamodb", endpoint_url="http://localhost:4566", region_name="eu-west-1")
    iam = boto3.client("iam", endpoint_url="http://localhost:4566", region_name="eu-west-1")
    kms = boto3.client("kms", endpoint_url="http://localhost:4566", region_name="eu-west-1")
    sfn = boto3.client("stepfunctions", endpoint_url="http://localhost:4566", region_name="eu-west-1")

    # 1. Create KMS key
    key_resp = kms.create_key(Description="Test KMS key")
    kms_key_arn = key_resp["KeyMetadata"]["Arn"]

    # 2. Create S3 buckets
    for bucket_name in ["mmc-test-baselines", "mmc-test-producer"]:
        try:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
            )
        except s3.exceptions.BucketAlreadyOwnedByYou:
            pass

    # 3. Create DynamoDB tables
    try:
        ddb.create_table(
            TableName="mmc-test-baseline-registry",
            KeySchema=[
                {"AttributeName": "project", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "project", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
    except ddb.exceptions.ResourceInUseException:
        pass

    try:
        ddb.create_table(
            TableName="mmc-test-outcomes",
            KeySchema=[
                {"AttributeName": "run_id", "KeyType": "HASH"},
                {"AttributeName": "analyser_type", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "run_id", "AttributeType": "S"},
                {"AttributeName": "analyser_type", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
    except ddb.exceptions.ResourceInUseException:
        pass

    # 4. Create IAM role for baseline writer
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        role_resp = iam.create_role(
            RoleName="mmc-test-baseline-writer",
            AssumeRolePolicyDocument=json.dumps(trust_policy),
        )
        baseline_writer_arn = role_resp["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role_resp = iam.get_role(RoleName="mmc-test-baseline-writer")
        baseline_writer_arn = role_resp["Role"]["Arn"]

    # 5. Create Step Functions state machine (simplified dummy)
    sfn_def = {
        "Comment": "Baseline analysis flow",
        "StartAt": "Pass",
        "States": {
            "Pass": {
                "Type": "Pass",
                "Result": {"status": "approved"},
                "End": True
            }
        }
    }

    try:
        sfn_resp = sfn.create_state_machine(
            name="mmc-test-baseline-sfn",
            definition=json.dumps(sfn_def),
            roleArn=baseline_writer_arn,
        )
        sfn_arn = sfn_resp["stateMachineArn"]
    except sfn.exceptions.StateMachineAlreadyExists:
        # Get existing
        sfn_resp = sfn.list_state_machines()
        matching = [sm for sm in sfn_resp["stateMachines"] if "baseline" in sm["name"]]
        sfn_arn = matching[0]["stateMachineArn"] if matching else None

    return {
        "kms_key_arn": kms_key_arn,
        "baselines_bucket": "mmc-test-baselines",
        "producer_bucket": "mmc-test-producer",
        "baseline_registry_table": "mmc-test-baseline-registry",
        "outcomes_table": "mmc-test-outcomes",
        "baseline_writer_role_arn": baseline_writer_arn,
        "baseline_sfn_arn": sfn_arn or "",
    }


if __name__ == "__main__":
    resources = create_localstack_infrastructure()
    print("Infrastructure created:")
    for key, value in resources.items():
        print(f"  {key}: {value}")
