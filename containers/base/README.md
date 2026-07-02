# mmc-base

Shared analyser base image + Python package (`mmc_base`).

Owns the container I/O contract: env-var parsing, S3/DDB/CloudWatch clients, failure sidecar, SageMaker ban-list guard, and a reusable contract-test harness. Analyser images extend this — they own only their math.

Design: [`../../docs/design/002-container-base.md`](../../docs/design/002-container-base.md).
