# AWS E2E Testing

Run the full system against real AWS services to validate production readiness.

## Quick start

```bash
export AWS_REGION=eu-west-1
export AWS_ACCOUNT_ID=123456789012
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

python3 scripts/aws-e2e-test.py --cleanup --verbose
```

Exit codes:
- `0` = all tests passed
- `4` = tests failed
- `1` = setup failed

## What gets tested

### Baseline Analysis
1. Upload test manifest and data to S3
2. Trigger baseline Step Functions state machine
3. Wait for execution to complete (max 5 min)
4. Assert:
   - Baseline registry entry created
   - All 5 analysers (mq, dq, bias, explain, shadow) succeeded
   - S3 has analyser output files

### Monitor Analysis
1. Directly invoke monitor Lambda function
2. Pass test predictions
3. Assert:
   - Outcomes table populated
   - 5 analyser results recorded
   - Results have expected structure

## Setup (one-time, manual)

### 1. Create AWS account for testing

Recommended: Separate AWS account (dev/test). If using shared account, ensure cleanup is enabled.

### 2. Create IAM user for CI/CD

Create a new IAM user with programmatic access:

```bash
aws iam create-user --user-name mmc-github-ci
aws iam create-access-key --user-name mmc-github-ci
```

Save the credentials (you'll only see them once).

### 3. Attach IAM policy

Create policy file `mmc-ci-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "cloudformation:*",
        "s3:*",
        "lambda:*",
        "dynamodb:*",
        "iam:*",
        "cloudwatch:*",
        "logs:*",
        "events:*",
        "stepfunctions:*",
        "ec2:*",
        "vpc:*",
        "kms:*",
        "sts:*"
      ],
      "Resource": "*"
    }
  ]
}
```

Attach to user:

```bash
aws iam put-user-policy --user-name mmc-github-ci \
  --policy-name MMCGitHubCIPolicy \
  --policy-document file://mmc-ci-policy.json
```

### 4. Add GitHub secrets

In your GitHub repository (Settings → Secrets → Actions):

1. `AWS_ACCOUNT_ID` — Your AWS account ID (12 digits)
2. `AWS_ACCESS_KEY_ID` — From step 2
3. `AWS_SECRET_ACCESS_KEY` — From step 2

Do NOT commit these to the repository.

### 5. Enable workflow

The workflow `.github/workflows/aws-e2e-test.yml` will run on pushes to main and on manual trigger.

## Running locally

### Prerequisites

- AWS credentials configured locally (IAM user from step 2)
- Python 3.11+
- Docker (for CDK asset bundling)
- `uv` package manager

### Run test

```bash
export AWS_REGION=eu-west-1
export AWS_ACCOUNT_ID=123456789012
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

python3 scripts/aws-e2e-test.py --cleanup --verbose
```

### Options

```bash
python3 scripts/aws-e2e-test.py --help

# Keep resources for debugging (manual cleanup required)
python3 scripts/aws-e2e-test.py --no-cleanup

# Verbose output
python3 scripts/aws-e2e-test.py --verbose

# Specific region
python3 scripts/aws-e2e-test.py --region us-east-1
```

### Using .env.local (local testing only)

Create `.env.local` (git-ignored) with credentials:

```bash
export AWS_REGION=eu-west-1
export AWS_ACCOUNT_ID=123456789012
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
```

Then:

```bash
source .env.local
python3 scripts/aws-e2e-test.py
```

## Test data

Test data is generated deterministically (same seed every run):

- **Training data:** 100 rows, 4 features (2 numeric, 2 categorical), 1 target
- **Predictions data:** 100 rows, with slight distribution shift to trigger drift detection
- **Size:** ~500 KB total (very small for fast testing)

See `tests/aws/fixtures/generate_test_data.py` for details.

## Troubleshooting

### CDK Deploy Fails

Check IAM permissions. Ensure policy has all required permissions for CDK (CloudFormation, S3, Lambda, DynamoDB, IAM, CloudWatch, Logs, Events, Step Functions, EC2, VPC, KMS, STS).

```bash
# Check if deploy worked
aws cloudformation list-stacks --region eu-west-1 \
  --query "StackSummaries[?StackName.contains(\`mmc-aws-test\`)]"
```

### Tests Timeout

Baseline analysis can take 3-5 min. If tests timeout, increase timeout in `.github/workflows/aws-e2e-test.yml`:

```yaml
timeout-minutes: 45  # Increased from 30
```

### Step Functions Execution Fails

Check Lambda and Step Functions logs:

```bash
# Get execution ARN
aws stepfunctions list-executions --state-machine-arn <ARN> \
  --region eu-west-1

# Get execution details
aws stepfunctions describe-execution --execution-arn <EXECUTION_ARN> \
  --region eu-west-1
```

### DynamoDB Tests Fail

Ensure `--cleanup` is used to delete tables between runs:

```bash
python3 scripts/aws-e2e-test.py --cleanup
```

If tables are orphaned:

```bash
aws dynamodb list-tables --region eu-west-1 | grep mmc-aws-test
aws dynamodb delete-table --table-name mmc-aws-test-baseline-registry --region eu-west-1
```

### Resource Not Found

Ensure stacks deployed successfully. Check CloudFormation:

```bash
aws cloudformation describe-stacks --region eu-west-1 \
  --query "Stacks[?contains(StackName, 'mmc-aws-test')]"
```

## Cost estimation

Per test run (assuming cleanup is enabled):

| Resource | Qty | Cost |
|----------|-----|------|
| Lambda invocations | 10+ | ~$0.10 |
| DynamoDB writes/reads | 100+ | ~$0.50 |
| S3 PUT/GET | 20+ | ~$0.01 |
| Data transfer | ~5MB | ~$0.00 |
| **Total** | | **~$0.60** |

Running daily: ~$18/month  
Running per commit (20/day): ~$12/month

Enable `--cleanup` to avoid storage charges for failed runs.

## CI/CD integration

The workflow runs automatically on:
1. **Pushes to main** — Validates production is ready
2. **Manual trigger** — On-demand testing or regions

Add badge to README:

```markdown
[![AWS E2E Tests](https://github.com/graylayer-labs/model-monitor-custom/actions/workflows/aws-e2e-test.yml/badge.svg)](https://github.com/graylayer-labs/model-monitor-custom/actions/workflows/aws-e2e-test.yml)
```

## Next steps

**Phase 2 (future):**
- Real workload testing (1000+ rows)
- Stress testing (parallel executions)
- Cost optimization validation
- Performance benchmarking

**Phase 3 (future):**
- Multi-region testing
- Disaster recovery validation
- Integration with Slack/PagerDuty alerts

## See also

- [LOCALSTACK_TESTING.md](LOCALSTACK_TESTING.md) — Local testing (no AWS)
- [CONFIGURATION.md](CONFIGURATION.md) — Deployment topology
- [ARCHITECTURE.md](ARCHITECTURE.md) — System design
