# CI/CD Setup: Two-Tier Testing Strategy

## Overview

Two GitHub Actions workflows handle E2E testing:

| Workflow | Trigger | Requirement | Time | Cost |
|----------|---------|-------------|------|------|
| **LocalStack** | Every PR | ✅ Must pass | ~5 min | Free (local) |
| **AWS** | Manual only | Approved users | ~5 min | Real AWS |

LocalStack tests catch regressions early and cheaply. AWS tests validate against real infrastructure.

## Setup (One-Time)

### 1. Add AWS Account ID Secret (for AWS workflow only)

1. Go to GitHub repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `AWS_ACCOUNT_ID`
4. Value: `204107103815`
5. Click **Add secret**

### 2. Create "AWS Testing" Environment (for access control)

1. Go to **Settings** → **Environments**
2. Click **New environment**
3. Name: `AWS Testing`
4. Click **Configure environment**
5. Under "Deployment branches": select **Protected branches only**
6. Under "Deployment protection rules" (Optional):
   - Add required reviewers if you want approval required
   - Or leave empty to allow any pusher to trigger
7. Click **Save protection rules**

### 3. Enable Branch Protection Rule

1. Go to **Settings** → **Branches**
2. Click **Add rule** under "Branch protection rules"
3. Apply to: `main`
4. Check the following:
   - ✅ Require a pull request before merging
   - ✅ Require status checks to pass before merging
     - Search for: `localstack-e2e-tests`
     - Select: `localstack-e2e-tests / localstack-e2e-tests`
   - ✅ Require branches to be up to date before merging
   - ✅ Dismiss stale pull request approvals when new commits are pushed
5. Click **Create**

**Note:** AWS tests are NOT required — only LocalStack tests block merging.

## How It Works

### LocalStack Tests (Required on Every PR)

1. **Trigger**: Automatically on every PR to main
2. **Setup**:
   - Starts LocalStack Docker container
   - Installs Python dependencies
   - No AWS credentials needed
3. **Test**: Runs full workflow (deploy → execute → verify)
4. **Result**:
   - ✅ If tests pass → PR can merge
   - ❌ If tests fail → PR is blocked, shows error details
5. **Speed**: ~5 minutes
6. **Cost**: Free (local Docker)

### AWS Tests (Manual Trigger, Approved Users Only)

1. **Trigger**: Manual via GitHub **Actions** tab → **AWS E2E Tests** → **Run workflow**
2. **Access**: Only users with "AWS Testing" environment approval can trigger
3. **Setup**:
   - Configures AWS credentials using GitHub OIDC (no stored tokens!)
   - Assumes test role in AWS account
4. **Test**: Runs against real AWS infrastructure
5. **Result**: Validates against production-like environment
6. **Speed**: ~5 minutes
7. **Cost**: Real AWS charges (minimal for test resources)

### Authentication (AWS Tests Only)

The workflow uses **GitHub OIDC** for AWS access:
- GitHub generates a temporary, job-scoped token
- Token assumes the IAM role configured in CloudFormation
- Role is restricted to GitHub repo + branch
- Credentials auto-expire when workflow completes
- **No long-lived keys stored in GitHub**

## Monitoring

### LocalStack Tests:
- View runs: **Actions** tab → `LocalStack E2E Tests (Required)`
- PR check status: Bottom of PR, under "Checks"
- Required to merge

### AWS Tests:
- View runs: **Actions** tab → `AWS E2E Tests (Manual)`
- Only appears if manually triggered
- For validation only, not required

## Troubleshooting

### LocalStack tests fail
- Check Docker is running
- Verify LocalStack service health (healthcheck in workflow)
- Check test data generation (see `tests/e2e/fixtures/`)

### AWS tests fail with "AWS credentials not found"
- Verify `AWS_ACCOUNT_ID` secret exists
- Verify OIDC trust relationship (see `docs/GITHUB_OIDC_SETUP.md`)
- Ensure you have "AWS Testing" environment approval

### Tests pass locally but fail in CI
- Check Python version (should be 3.12)
- Check region (should be eu-west-1)
- Check AWS resource availability

### Rate limiting or quota issues
- AWS tests create real resources (watch costs)
- LocalStack tests are free but slower if Docker is slow
- AWS tests include automatic cleanup
