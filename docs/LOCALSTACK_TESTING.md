# LocalStack End-to-End Testing

This guide covers running the complete model-monitor-custom system locally using LocalStack, without requiring a real AWS account.

## Quick Start

Run the comprehensive test suite with a single command:

```bash
python scripts/localstack-test-runner.py
```

This automatically:
1. Starts LocalStack (or verifies it's running)
2. Provisions AWS resources (S3, DynamoDB, IAM, KMS) via boto3
3. Executes pytest E2E tests against real infrastructure
4. Stops LocalStack and reports results

**Exit codes:**
- `0` = all tests passed
- `1` = LocalStack setup failed
- `4` = test execution failed

## System Requirements

- **Docker** (Desktop or daemon running)
- **Python 3.11+** with `uv` package manager
- **curl** (for LocalStack health checks)

Verify dependencies:

```bash
docker --version
uv --version
which curl
```

Note: No AWS CLI, cdklocal, or npm required—tests use Python boto3 for infrastructure creation.

## How It Works

### Architecture

The test system creates and validates the monitoring data flow locally:

```
LocalStack Services
├── S3 (baseline artifacts, configuration)
├── DynamoDB (baseline registry)
├── KMS (encryption keys)
└── IAM (service roles)
```

### Workflow

1. **LocalStack Startup** (`docker-compose.localstack.yml`)
   - Starts LocalStack container with required services
   - Waits for health check (S3 available within 60s)

2. **Infrastructure Setup** (via Python boto3 in `tests/e2e/manual_infra.py`)
   - Creates S3 buckets (baselines, producer)
   - Creates DynamoDB tables (baseline registry, outcomes)
   - Creates KMS encryption key
   - Creates IAM service roles

3. **Test Execution** (via pytest in `tests/e2e/test_localstack_simple.py`)
   - `test_baseline_registry_operations()` — validates DynamoDB operations
   - `test_s3_operations()` — validates S3 artifact storage
   - `test_baseline_workflow()` — validates complete baseline approval workflow

4. **LocalStack Shutdown**
   - Tears down containers and volumes
   - (Can skip with `--no-cleanup` for debugging)

## Usage

### Run Full Test Suite

```bash
python scripts/localstack-test-runner.py
```

Expected output:
```
[•] Starting LocalStack...
[•] Waiting for LocalStack to become healthy...
[✓] LocalStack is healthy
[•] Running pytest E2E tests (manual infrastructure)...

tests/e2e/test_localstack_simple.py::test_baseline_registry_operations PASSED [ 33%]
tests/e2e/test_localstack_simple.py::test_s3_operations PASSED           [ 66%]
tests/e2e/test_localstack_simple.py::test_baseline_workflow PASSED       [100%]

============================================================
[✓] All tests PASSED
============================================================
```

### Verbose Output

For detailed debugging:

```bash
python scripts/localstack-test-runner.py --verbose
```

Prints:
- Command execution details
- AWS resource creation logs
- pytest output

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

# View CloudWatch logs (if running Lambda tests)
aws logs describe-log-groups \
  --endpoint-url http://localhost:4566
```

## Manual Testing

To run tests manually (without the test runner):

```bash
# 1. Start LocalStack
docker compose -f docker-compose.localstack.yml up -d

# 2. Wait for health
curl -f http://localhost:4566/_localstack/health

# 3. Run tests
LOCALSTACK_TEST_ENABLED=1 uv run pytest tests/e2e/test_localstack_simple.py -v

# 4. Tear down
docker compose -f docker-compose.localstack.yml down -v
```

## Test Structure

### `tests/e2e/manual_infra.py`

Infrastructure-as-Python: creates all AWS resources using boto3.

```python
resources = create_localstack_infrastructure()
# Returns dict with:
# - kms_key_arn
# - baselines_bucket
# - producer_bucket
# - baseline_registry_table
# - outcomes_table
# - baseline_writer_role_arn
# - baseline_sfn_arn
```

**Key features:**
- Idempotent: handles `BucketAlreadyOwnedByYou`, `ResourceInUseException`, `EntityAlreadyExistsException`
- Allows tests to run repeatedly on same LocalStack container

### `tests/e2e/test_localstack_simple.py`

Three E2E tests:

1. **test_baseline_registry_operations()** — Validates DynamoDB table structure and CRUD operations
   - Creates a baseline registry entry
   - Reads it back
   - Verifies schema

2. **test_s3_operations()** — Validates S3 artifact storage
   - Uploads baseline manifest JSON
   - Reads it back
   - Verifies content

3. **test_baseline_workflow()** — Validates complete baseline approval workflow
   - Uploads manifest and analyser outputs to S3
   - Creates baseline registry entry with approval status
   - Verifies all 5 analysers (mq, dq, bias, explain, shadow) are approved
   - Validates end-to-end data consistency

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

### Tests Fail with Permission Errors

```
An error occurred (AccessDenied) when calling the ...
```

**Ensure LocalStack is fully healthy:**

```bash
curl http://localhost:4566/_localstack/health | jq
```

Wait until all required services show `"running"` or `"available"`:
```json
{
  "services": {
    "s3": "running",
    "dynamodb": "running",
    "iam": "running",
    "kms": "running"
  }
}
```

### Import Errors in Tests

```
ModuleNotFoundError: No module named 'manual_infra'
```

Ensure `PYTHONPATH` includes the test directory:

```bash
export PYTHONPATH=/path/to/tests/e2e:$PYTHONPATH
python -m pytest tests/e2e/test_localstack_simple.py
```

## Verification Checklist

After running tests, verify:

- [ ] All 3 tests passed (exit code 0)
- [ ] Baseline registry table created with test data
- [ ] S3 artifacts readable
- [ ] All 5 analysers have approval status in registry
- [ ] No errors in pytest output

## Performance Notes

Typical execution times (on modern hardware):

| Phase | Duration |
|-------|----------|
| LocalStack startup | 10-15s |
| Infrastructure setup | 1-2s |
| pytest execution | 5-10s |
| LocalStack teardown | 2-5s |
| **Total** | ~20-30s |

Much faster than CDK-based approach (which was 90-165s).

## Future Enhancements

Future test additions (not blocking current work):
- Lambda invocation tests (once Dockerfile.lambda variants created)
- Step Functions execution tests (baseline and monitor state machines)
- CloudWatch metric validation
- End-to-end analyser pipeline integration

For now, these manual infrastructure tests provide a solid foundation for validating the data model and AWS resource structure.

## See Also

- [CDK Documentation](cdk/README.md) — Infrastructure-as-code setup
- [Configuration Guide](CONFIGURATION.md) — Project and account configuration
- [Testing Standards](STANDARDS.md) — Testing conventions
