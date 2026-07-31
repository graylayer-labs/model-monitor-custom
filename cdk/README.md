# model-monitor-cdk

CDK constructs for the model-monitor-custom project.

## Local Testing (LocalStack)

Run the full monitoring stack locally without AWS credentials or real cloud resources.

**Prerequisites:**
```bash
npm install -g aws-cdk aws-cdk-local
# docker and docker-compose should already be installed
```

**Run end-to-end test:**
```bash
LOCALSTACK_TEST_ENABLED=1 uv run pytest tests/e2e/test_localstack_inference_monitor.py -v
```

This:
1. Boots LocalStack in Docker
2. Builds analyser container images locally
3. Deploys the monitoring stack (Lambda backend)
4. Seeds sample data
5. Runs a full analysis fan-out
6. Verifies outcomes in DynamoDB

First run takes ~5 minutes (container image builds); subsequent runs ~30 seconds (cached).
