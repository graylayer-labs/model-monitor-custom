# CI/CD Setup: AWS E2E Tests Required on PRs

## Overview

The repository now has a GitHub Actions workflow (`aws-e2e-tests.yml`) that automatically runs the AWS E2E test suite on every pull request. This ensures infrastructure changes are validated before merging to main.

## Setup (One-Time)

### 1. Add AWS Account ID Secret

1. Go to GitHub repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `AWS_ACCOUNT_ID`
4. Value: `204107103815`
5. Click **Add secret**

### 2. Enable Branch Protection Rule

1. Go to **Settings** → **Branches**
2. Click **Add rule** under "Branch protection rules"
3. Apply to: `main`
4. Check the following:
   - ✅ Require a pull request before merging
   - ✅ Require status checks to pass before merging
     - Search for: `e2e-tests`
     - Select: `e2e-tests / e2e-tests`
   - ✅ Require branches to be up to date before merging
   - ✅ Dismiss stale pull request approvals when new commits are pushed
5. Click **Create**

## How It Works

### On Every Pull Request:

1. **Trigger**: Workflow runs automatically when PR is opened/updated
2. **Setup**: 
   - Checks out code
   - Installs Python dependencies via uv
   - Configures AWS credentials using GitHub OIDC (no stored tokens!)
3. **Test**: Runs `python3 scripts/aws-e2e-test.py --verbose`
4. **Result**: 
   - ✅ If tests pass → PR can be merged
   - ❌ If tests fail → PR is blocked, shows error details

### Authentication

The workflow uses **GitHub OIDC** for AWS access:
- GitHub generates a temporary, job-scoped token
- Token assumes the IAM role configured in CloudFormation
- Role is restricted to GitHub repo + branch
- Credentials auto-expire when workflow completes
- **No long-lived keys stored in GitHub**

## Monitoring

- View workflow runs: **Actions** tab → `AWS E2E Tests`
- View PR check status: At bottom of PR, under "Checks"
- Logs: Click workflow run → `e2e-tests` job for full output

## Troubleshooting

### Workflow fails with "AWS credentials not found"
- Verify `AWS_ACCOUNT_ID` secret exists in repo settings
- Verify the OIDC trust relationship is set up (see `docs/GITHUB_OIDC_SETUP.md`)

### Tests pass locally but fail in CI
- Likely environment difference. Check:
  - Python version (should be 3.12)
  - AWS region (should be eu-west-1)
  - Available AWS resources

### Rate limiting or quota issues
- Each workflow run creates real AWS resources
- Default behavior: cleanup after each run
- If tests run frequently, watch for AWS costs
