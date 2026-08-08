# GitHub OIDC Setup for AWS E2E Testing

Modern, secure CI/CD authentication using GitHub OIDC. No long-lived credentials stored in GitHub.

## How it works

```
GitHub Action runs
    ↓
Requests token from GitHub's OIDC provider
    ↓
Assumes AWS IAM role (via OIDC trust relationship)
    ↓
AWS returns short-lived credentials (1 hour max)
    ↓
Test runs with temporary credentials
    ↓
Credentials auto-expire
```

**Benefits:**
- ✅ Zero secrets to leak
- ✅ Automatic credential rotation (1 hour)
- ✅ AWS audit log shows which GitHub repo
- ✅ Can't accidentally commit credentials
- ✅ Industry standard (Netflix, Uber, etc.)

## Setup (15 minutes, one-time)

### Step 1: Create OIDC Provider in AWS

In your AWS account, create the OIDC provider that trusts GitHub:

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 \
  --region us-east-1
```

**Note:** This is global (region doesn't matter). Save the output, you'll need the ARN.

### Step 2: Deploy IAM Role via CloudFormation

Use the template at [`docs/github-oidc-setup.yaml`](github-oidc-setup.yaml).

**In AWS CloudShell or CLI:**

```bash
# Get your GitHub repo info
GITHUB_REPO="graylayer-labs/model-monitor-custom"  # Replace with your repo
GITHUB_BRANCH="main"

# Deploy the CloudFormation stack
aws cloudformation deploy \
  --template-file docs/github-oidc-setup.yaml \
  --stack-name mmc-github-oidc \
  --parameter-overrides \
    GitHubRepo=$GITHUB_REPO \
    GitHubBranch=$GITHUB_BRANCH \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1

# Get the role ARN (you'll need this)
aws cloudformation describe-stacks \
  --stack-name mmc-github-oidc \
  --query "Stacks[0].Outputs[?OutputKey=='RoleArn'].OutputValue" \
  --output text \
  --region us-east-1
```

**Output should look like:**
```
arn:aws:iam::123456789012:role/mmc-github-e2e-test-role
```

### Step 3: Add GitHub Secret

In GitHub repo (Settings → Secrets and variables → Actions):

1. Click "New repository secret"
2. Name: `AWS_ROLE_ARN`
3. Value: (paste the ARN from Step 2)
4. Click "Add secret"

That's it! No other secrets needed.

## Verify Setup

### Test 1: Manual workflow trigger

1. Go to GitHub repo → Actions
2. Select "AWS E2E Test" workflow
3. Click "Run workflow" → main branch → "Run workflow"
4. Watch it execute (should complete in ~5 min)

### Test 2: Push to main

Any commit to main should trigger the workflow automatically.

## Troubleshooting

### "InvalidParameterException: Role is not assumable"

The CloudFormation template didn't deploy correctly. Check:
1. Stack exists: `aws cloudformation list-stacks | grep mmc-github-oidc`
2. Stack status: Should be `CREATE_COMPLETE`
3. OIDC provider exists: `aws iam list-open-id-connect-providers`

### "AssumeRoleUnauthorized"

The OIDC provider trust relationship is wrong. Check:
1. GitHub repo name matches in CloudFormation template
2. GitHub branch matches (default: `main`)
3. OIDC provider ARN is correct

**Debug:**
```bash
# Get the trust policy
aws iam get-role --role-name mmc-github-e2e-test-role \
  --query "Role.AssumeRolePolicyDocument" \
  --output json
```

### Workflow can't write to AWS

The IAM policy might be missing permissions. Check CloudFormation template has all required services:
- CloudFormation
- S3
- Lambda
- DynamoDB
- IAM
- CloudWatch
- Events
- Step Functions
- EC2/VPC
- KMS

If missing, update the template and redeploy:
```bash
aws cloudformation update-stack \
  --stack-name mmc-github-oidc \
  --template-body file://docs/github-oidc-setup.yaml \
  --parameter-overrides GitHubRepo=$GITHUB_REPO GitHubBranch=$GITHUB_BRANCH \
  --capabilities CAPABILITY_NAMED_IAM
```

## Local Testing

If you want to test locally using the same IAM role:

```bash
# Assume the role and get credentials
ROLE_ARN="arn:aws:iam::123456789012:role/mmc-github-e2e-test-role"
SESSION=$(aws sts assume-role --role-arn $ROLE_ARN --role-session-name test-session)

# Extract credentials
export AWS_ACCESS_KEY_ID=$(echo $SESSION | jq -r '.Credentials.AccessKeyId')
export AWS_SECRET_ACCESS_KEY=$(echo $SESSION | jq -r '.Credentials.SecretAccessKey')
export AWS_SESSION_TOKEN=$(echo $SESSION | jq -r '.Credentials.SessionToken')
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=eu-west-1

# Run test
python3 scripts/aws-e2e-test.py --cleanup --verbose
```

## Security Notes

### What's being trusted?
- GitHub's OIDC provider (`token.actions.githubusercontent.com`)
- Your specific GitHub repo
- Your specific branch (default: `main`)

### What can't be abused?
- Someone can't use this to assume the role from their personal computer
- Someone can't assume the role from a different GitHub repo
- Someone can't assume the role from a different branch
- Credentials auto-expire (1 hour)

### Best practices
1. Restrict the role to your repo + branch (done by template)
2. Review CloudFormation policy permissions (adjust as needed)
3. Rotate the template annually (update thumbprint if GitHub updates it)
4. Monitor AWS CloudTrail for unexpected role assumptions

## Cleanup

To remove OIDC setup (reverse the changes):

```bash
# Delete the CloudFormation stack
aws cloudformation delete-stack --stack-name mmc-github-oidc

# Delete the OIDC provider (manual)
PROVIDER_ARN=$(aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[0].Arn" --output text)
aws iam delete-open-id-connect-provider --open-id-connect-provider-arn $PROVIDER_ARN

# Remove the GitHub secret (manual)
# GitHub repo → Settings → Secrets → Delete AWS_ROLE_ARN
```

## See also

- [GitHub docs: About OpenID Connect](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/about-security-hardening-with-openid-connect)
- [AWS docs: OpenID Connect identity providers](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html)
- [AWS Actions: Configure AWS Credentials](https://github.com/aws-actions/configure-aws-credentials)
