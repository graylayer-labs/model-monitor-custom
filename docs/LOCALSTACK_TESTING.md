# LocalStack End-to-End Testing

This guide covers running the complete model-monitor-custom system locally using LocalStack, without requiring a real AWS account.

## Quick Start

Run the comprehensive test suite with a single command:

```bash
python scripts/localstack-test-runner.py
```

This automatically:
1. Starts LocalStack (or verifies it's running)
2. Builds Docker images for Lambda container functions
3. Provisions AWS resources (S3, DynamoDB, Lambda, Step Functions, IAM, KMS)
4. Runs CDK bootstrap and deployment
5. Executes baseline and monitor E2E tests
6. Stops LocalStack and reports results

## System Requirements

- **Docker** (Desktop or daemon running)
- **Python 3.11+** with `uv` package manager
- **AWS CLI v2** (for endpoint configuration)
- **cdklocal** (CDK wrapper for LocalStack): `npm install -g aws-cdk-local`
- **curl** (for LocalStack health checks)

Verify dependencies:

```bash
docker --version
uv --version
aws --version
which cdklocal
which curl
```

## How It Works

### Architecture

The test system runs the full monitoring pipeline locally:

```
LocalStack Services
├── S3 (baseline artifacts, configuration)
├── DynamoDB (baseline registry)
├── Lambda (analyser functions + orchestration)
├── Step Functions (baseline & monitor state machines)
├── ECR (container image references)
├── KMS (encryption keys)
├── IAM (service roles)
└── CloudWatch (logs & metrics)
```

### Workflow

1. **LocalStack Startup** (`docker-compose.localstack.yml`)
   - Starts LocalStack container with required services
   - Waits for health check (S3 available within 60s)

2. **Docker Image Building**
   - Builds `mmc-base-lambda:latest` from `containers/base/Dockerfile.lambda`
   - Builds analyser images: `mmc-{mq,dq,bias,explain,shadow}-lambda:latest`
   - Each based on `public.ecr.aws/lambda/python:3.12`

3. **Test Execution** (via pytest)
   - `test_localstack_baseline.py`
     - Baseline snapshot validation workflow
     - LoadAndGate → Parallel analysers → EvaluateResults → WriteRegistry
   - `test_localstack_inference_monitor.py`
     - Online inference monitoring workflow
     - Activation → Parallel analysers → Outcomes

4. **LocalStack Shutdown**
   - Tears down containers and volumes
   - (Can skip with `--no-cleanup` for debugging)

## Usage

### Run Full Test Suite

```bash
python scripts/localstack-test-runner.py
```

**Exit codes:**
- `0` = all tests passed
- `1` = LocalStack setup failed
- `2` = Docker image build failed
- `3` = CDK bootstrap/deploy failed
- `4` = test execution failed

### Verbose Output

For detailed debugging:

```bash
python scripts/localstack-test-runner.py --verbose
```

Prints:
- Command execution details
- AWS resource creation logs
- Docker build output
- pytest full output

### Keep LocalStack Running (Debugging)

To investigate failures without tearing down LocalStack:

```bash
python scripts/localstack-test-runner.py --no-cleanup
```

Then inspect resources:

```bash
# List S3 buckets
aws s3 ls --endpoint-url http://localhost:4566

# Query DynamoDB table
aws dynamodb scan \
  --table-name mmc-test-baseline-registry \
  --endpoint-url http://localhost:4566

# View Step Functions executions
aws stepfunctions list-executions \
  --state-machine-arn arn:aws:states:eu-west-1:000000000000:stateMachine:... \
  --endpoint-url http://localhost:4566

# View CloudWatch logs
aws logs describe-log-groups \
  --endpoint-url http://localhost:4566

# View Lambda function errors
aws logs tail /aws/lambda/ --follow \
  --endpoint-url http://localhost:4566
```

## Manual Testing

To run tests manually (without the test runner):

```bash
# 1. Start LocalStack
docker compose -f docker-compose.localstack.yml up -d

# 2. Build images
docker build -f containers/base/Dockerfile.lambda \
  -t mmc-base-lambda:latest containers/base

for analyser in mq dq bias explain shadow; do
  docker build -f containers/$analyser/Dockerfile.lambda \
    -t mmc-$analyser-lambda:latest \
    --build-arg BASE_IMAGE=mmc-base-lambda:latest \
    containers/$analyser
done

# 3. Bootstrap CDK
AWS_ENDPOINT_URL_S3=http://localhost:4566 \
AWS_ENDPOINT_URL_DYNAMODB=http://localhost:4566 \
AWS_ENDPOINT_URL_STEPFUNCTIONS=http://localhost:4566 \
AWS_ENDPOINT_URL_LAMBDA=http://localhost:4566 \
AWS_ENDPOINT_URL_IAM=http://localhost:4566 \
AWS_ENDPOINT_URL_KMS=http://localhost:4566 \
AWS_ENDPOINT_URL_LOGS=http://localhost:4566 \
cdklocal bootstrap aws://000000000000/eu-west-1

# 4. Deploy CDK
cdklocal deploy --require-approval never --all

# 5. Run tests
LOCALSTACK_TEST_ENABLED=1 uv run pytest tests/e2e/ -m e2e -v

# 6. Tear down
docker compose -f docker-compose.localstack.yml down -v
```

## Troubleshooting

### LocalStack Unhealthy

```
ERROR LocalStack did not become healthy within 60s
```

**Check LocalStack status:**

```bash
docker ps | grep localstack
docker logs $(docker ps -q -f ancestor=localstack/localstack)
```

**Common causes:**
- Docker daemon not running: `open -a Docker`
- Port 4566 in use: `lsof -i :4566`
- Insufficient resources: check Docker Desktop settings (Memory, CPU)

### Docker Image Build Fails

```
ERROR Step X/Y : ... failed
```

**Check Docker output:**

```bash
docker build -f containers/base/Dockerfile.lambda \
  -t mmc-base-lambda:latest containers/base
```

**Common causes:**
- Missing `uv` in base Lambda image: check `containers/base/Dockerfile.lambda`
- Network issues: `docker build --no-cache`

### CDK Bootstrap Fails

```
ERROR Failed to publish asset ...
```

**Verify LocalStack is running:**

```bash
curl -f http://localhost:4566/_localstack/health
```

**Check CDK environment variables are set:**

```bash
echo $AWS_ENDPOINT_URL_S3
echo $AWS_ENDPOINT_URL_DYNAMODB
# ... etc
```

### Tests Timeout

```
TimeoutError: Baseline execution did not complete within 30s
```

**Increase timeout in test:**

In `tests/e2e/test_localstack_baseline.py`, increase `timeout = 30` to `timeout = 60` or higher.

**Debug SFN execution:**

```bash
# Find execution ARN from test output
aws stepfunctions describe-execution \
  --execution-arn arn:aws:states:eu-west-1:000000000000:execution:... \
  --endpoint-url http://localhost:4566
```

### Lambda Function Fails

```
ExecutionFailed: One or more analysers failed
```

**View Lambda logs:**

```bash
aws logs tail /aws/lambda/mmc-test-mq-fn --follow \
  --endpoint-url http://localhost:4566
```

**Debug locally:**

```bash
docker run -it \
  -e AWS_ENDPOINT_URL_S3=http://host.docker.internal:4566 \
  -e PROJECT_NAME=test-project \
  -e ANALYSER_TYPE=mq \
  mmc-mq-lambda:latest
```

## Architecture Notes

### Compute Backend Toggle

The system defaults to Lambda for LocalStack testing (better supported by LocalStack). ECS remains available for real AWS by setting `compute_backend: ecs` in `cdk/environments/projects.yaml`.

### Image Loading

Lambda functions are built as Docker container images (`PackageType: Image`) for consistency with production Fargate deployment. LocalStack's Lambda executor:
- Pulls images from local Docker daemon
- Executes containers with mounted volumes for code execution

### State Machine Shape

Both baseline and monitor Step Functions use identical retry/catch patterns whether running on Lambda or ECS, ensuring test results reflect production behavior.

## Configuration

### `cdk/environments/projects.yaml`

Project configuration for LocalStack:

```yaml
projects:
  - name: test-model
    inference_account: "000000000000"  # LocalStack account ID
    producer_bucket_arn: "arn:aws:s3:::test-training-data"
    compute_backend: lambda            # Use Lambda (not ecs)
    vpc_id: null                        # Lambda doesn't need VPC
    schedule: "cron(0 * * * ? *)"
```

### `cdk/environments/accounts.yaml`

Account configuration:

```yaml
region: us-east-1
roles:
  artifact: "000000000000"
  operations: "000000000000"
  inference:
    - "000000000000"
operations_vpc_id: null
```

### `docker-compose.localstack.yml`

Services enabled:
- `S3` — object storage for baselines and config
- `DynamoDB` — baseline registry table
- `Lambda` — analyser and orchestration functions
- `StepFunctions` — workflow orchestration
- `ECR` — container image registry references
- `IAM` — service roles and permissions
- `KMS` — encryption keys
- `CloudWatch` — logs and metrics (basic support)

## Verification Checklist

After running tests, verify:

- [ ] All tests passed (exit code 0)
- [ ] Baseline registry table has approved entry for `test-project/v7`
- [ ] All 5 analysers (mq, dq, bias, explain, shadow) have `ok` status
- [ ] Baseline execution completed within timeout
- [ ] No Lambda function errors in CloudWatch logs
- [ ] Monitor execution (if enabled) also succeeded

## Performance Notes

Typical execution times (on modern hardware):

| Phase | Duration |
|-------|----------|
| LocalStack startup | 10-15s |
| Docker image builds | 30-60s |
| CDK bootstrap | 5-10s |
| CDK deploy | 15-30s |
| Baseline E2E test | 10-20s |
| Monitor E2E test | 10-20s |
| LocalStack teardown | 5-10s |
| **Total** | ~90-165s |

## Development Workflow

When developing changes to the system:

1. **Make code changes** (CDK, Lambda handlers, analysers)
2. **Run test suite** `python scripts/localstack-test-runner.py`
3. **Verify** tests pass with real code changes (not mocked)
4. **Debug** with `--no-cleanup` and manual inspection if needed
5. **Commit** only after tests pass

### Iterative Testing

For faster iteration during development:

```bash
# Terminal 1: Keep LocalStack running
python scripts/localstack-test-runner.py --no-cleanup

# Terminal 2: Make changes and run specific tests
LOCALSTACK_TEST_ENABLED=1 uv run pytest tests/e2e/test_localstack_baseline.py -v -s
```

## See Also

- [CDK Documentation](cdk/README.md) — Infrastructure-as-code setup
- [Configuration Guide](CONFIGURATION.md) — Project and account configuration
- [Testing Standards](STANDARDS.md) — Testing conventions
