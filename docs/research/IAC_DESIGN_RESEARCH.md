# IaC Design Research — model-monitor-custom

**Purpose.** Ground the CDK layer of `model-monitor-custom` (batch analysis on ECS Fargate + SFN Standard, multi-account) in real public references, not opinion. Every non-inference claim below links to a source; where evidence is thin the section explicitly says `no public reference found, my inference from X`.

**Runtime already decided** (see `docs/SFN_FAN_OUT_RESEARCH.md`, `docs/SFN_STATE_STRUCTURE_RESEARCH.md`):
- ECS Fargate via `ecs:runTask.sync`
- SFN Standard + Parallel + per-branch Retry/Catch
- Two workflows: snapshot (S3-triggered) and live (cron-triggered)
- Accounts: `ml-artifact` (registry), `ml-operations` (baseline compute), `ml-inference-*` (endpoints + live monitor)
- Every construct takes account IDs + role ARNs as inputs — no hardcoded accounts

**Repo shape already committed:** `containers/` + `cdk/` + `docs/` + `tests/` in one tree.

---

## Part A — Multi-account CDK Python reference architectures

Three real repos examined, plus the AWS ML solution reference.

### A.1 aws-samples/aws-cdk-pipelines-datalake-infrastructure

Source: https://github.com/aws-samples/aws-cdk-pipelines-datalake-infrastructure

The most directly relevant reference for our shape (multi-account, per-env stacks, CDK Pipelines-driven).

| Question | Finding |
|---|---|
| Language | CDK Python. `app.py`, `requirements.txt`, `python3 -m venv .venv`. |
| Stacks per target account | Three application stacks per env: **IAM**, **S3BucketZones** (buckets + KMS), **VPC** (VPC, SGs, endpoints). Plus the pipeline stack itself in the deployment account. |
| Grouping rule | One stack per concern (identity, storage, network). Not one giant stack. Not one stack per resource — mid-grained. |
| Account IDs threaded | Python config module `lib/configuration.py` with a `local_mapping` dict keyed by `DEPLOYMENT`/`DEV`/`TEST`/`PROD`. Values like `ACCOUNT_ID`, `REGION`, `VPC_CIDR` live there and are committed. |
| Deploy tool | CDK Pipelines (`pipelines.CodePipeline`). Self-mutating. Branch → env: `main`→dev, `test`→test, `prod`→prod. |
| Cross-account trust | Explicit shell script `./lib/prerequisites/bootstrap_target_account.sh <deployment_account_id> arn:aws:iam::aws:policy/AdministratorAccess`. |
| Naming | Two config keys: `LOGICAL_ID_PREFIX = 'DataLakeCDKBlog'` (CamelCase) and `RESOURCE_NAME_PREFIX = 'cdkblog-e2e'` (kebab, for globally-unique physical names). |
| Tagging | Centralised in `lib/tagging.py`. README doesn't disclose whether via Aspects or direct `Tags.of(...).add()` — not extractable from README alone. |

**Takeaway.** Small number of stacks per account (3), grouped by concern. Config-as-Python-dict, not `cdk.json` context. CDK Pipelines with bootstrap `--trust` script.

### A.2 awslabs/aws-serverless-data-lake-framework (SDLF)

Source: https://github.com/awslabs/aws-serverless-data-lake-framework

Bigger, more mature, worth noting as a **counter-example** — SDLF is not CDK.

| Question | Finding |
|---|---|
| Language | Python + Shell. **CloudFormation templates, not CDK.** Config files include `.cfn-nag-deny-list.yml`, `.cfnlintrc`. Topic tags don't include `cdk`. |
| Modules | `sdlf-cicd`, `sdlf-foundations`, `sdlf-dataset`, `sdlf-pipeline`, `sdlf-monitoring`, `sdlf-team`, plus `sdlf-stage-*` per compute engine (`sdlf-stage-ecsfargate`, `sdlf-stage-glue`, `sdlf-stage-lambda`, `sdlf-stage-emrserverless`, `sdlf-stage-dataquality`). |
| Deploy | Shell scripts + CodeBuild: `./deploy-role.sh -p <profile> datalake` then `./deploy-cicd.sh -p <profile> datalake`, then start the `sdlf-cicd-datalake` CodeBuild project. |
| Published as library | Not published to pip/npm. Consumers download release tarballs: `curl -L -O .../refs/tags/2.11.0.tar.gz`. |

**Takeaway.** SDLF is a valid grouping model (foundation / dataset / pipeline / monitoring / team / per-stage-engine) but delivered as CFN not CDK, and consumed via tarball. Not a template for us to copy structurally, but the **module boundaries** are worth stealing — foundation/pipeline/monitoring/team is a clean separation.

### A.3 aws-solutions/mlops-workload-orchestrator (archived Jun 2025)

Source: https://github.com/aws-solutions/mlops-workload-orchestrator

| Question | Finding |
|---|---|
| Language | CDK Python (97.2% Python, Python 3.10). |
| Top-level stack | `source/infrastructure/lib/mlops_orchestrator_stack.py`. Under it: `blueprints/ml_pipelines/` (stacks), `blueprints/pipeline_definitions/` (constructs), `blueprints/lambdas/` (handler code), `blueprints/aspects/`. |
| Multi-account model | Hub/spoke via AWS Organizations + **CloudFormation StackSets** (not CDK Pipelines). Hub is the orchestrator/management account; spokes are dev/staging/prod. Lambda `create_update_cf_stackset` runs StackSet ops. |
| Deploy mechanism | CDK synthesises CFN templates → build script `deployment/build-s3-dist.sh` → upload to S3 → deploy via CloudFormation console. Not `cdk deploy` end-to-end. |
| Account IDs configured | Not explicit in README — most likely CFN template parameters (inferred, not confirmed). |
| Repo layout | `blueprints/aspects/` folder confirms Aspects are used for cross-cutting concerns (tagging, validation). |

**Takeaway.** Interesting structure: **constructs and stacks are cleanly separated** (`pipeline_definitions/` vs `ml_pipelines/`), Aspects are a first-class folder, StackSets rather than CDK Pipelines for cross-account. Archived — AWS steered users to SageMaker Unified Studio — so don't take it as current best practice, but the layer split is sound.

### A.4 aws-samples/aws-cdk-examples (Python)

Source: https://github.com/aws-samples/aws-cdk-examples/tree/master/python

Grab-bag of small examples. Relevant ones:
- `cross-account-eventbridge-in-organization` — org-wide EventBridge across accounts.
- `cross-stack-resources` — foundational cross-stack references.
- `codepipeline-docker-build` — pipeline that builds Docker images (relevant to our ECR + CDK combo).
- `codepipeline-build-deploy-github-manual` — GitHub source, manual approval.

None is a full multi-account ML platform. Use these as **snippet libraries** rather than architecture.

### Consolidated Part A findings

| Signal | Evidence |
|---|---|
| **Mid-grained stack count per account** (3-5), not one mega-stack, not per-resource | datalake-infrastructure (3), mlops-workload-orchestrator (~4-5), SDLF module boundaries |
| **Config-as-Python-dict** beats `cdk.json` context for account IDs | datalake-infrastructure `configuration.py`. `cdk.json` context is treated as brittle in AWS Prescriptive Guidance — no direct quote here. |
| **Constructs vs Stacks folder separation** | mlops-workload-orchestrator (`pipeline_definitions/` vs `ml_pipelines/`); AWS Prescriptive Guidance recommends a `common/` folder for construct factories. |
| **Nobody publishes the CDK library to pip** | SDLF ships tarball; datalake-infrastructure and mlops-workload-orchestrator are consumed as a repo, not a package. |

---

## Part B — Deploy story options for 5+ accounts

### B.1 CDK Pipelines (`pipelines.CodePipeline`)

Source: AWS CDK Developer Guide + datalake-infrastructure repo.

**What it provisions** (from general docs knowledge — the `cdk_pipeline.html` page was empty when fetched, so the concrete resource list is `no public reference in this fetch, my inference from CDK API docs and prior deploys`):
- One AWS CodePipeline
- CodeBuild projects for Synth, SelfMutate, asset publishing
- S3 artifact bucket (KMS-encrypted when `cross_account_keys=True`)
- Cross-account IAM roles
- Support stacks in target accounts (replication buckets + KMS)

**Real Python example** (pattern from datalake-infrastructure + CDK API):

```python
pipeline = pipelines.CodePipeline(
    self, "Pipeline",
    cross_account_keys=True,
    synth=pipelines.ShellStep("Synth",
        input=pipelines.CodePipelineSource.git_hub("org/repo", "main"),
        commands=["pip install -r requirements.txt", "npx cdk synth"],
    ),
)
pipeline.add_stage(AppStage(self, "Dev",
    env=Environment(account="111111111111", region="eu-west-1")))
pipeline.add_stage(AppStage(self, "Prod",
    env=Environment(account="222222222222", region="eu-west-1")))
```

**Self-mutation.** Every pipeline run first synths + deploys the pipeline itself; then restarts execution so downstream stages use the new definition. Documented at https://docs.aws.amazon.com/cdk/v2/guide/cdk_pipeline.html (page currently returns near-empty content — this behaviour is CDK canon and observable in datalake-infrastructure's `pipeline_stack.py`).

**Cost.** From https://aws.amazon.com/codepipeline/pricing/:
- V1: **$1.00 / active pipeline / month**. One free active pipeline per month per account. Free for first 30 days after creation.
- V2: **$0.002 / action-execution-minute**. 100 free action-minutes per month per account.
- Plus S3 storage for artifacts, plus underlying CodeBuild charges (CodeBuild pricing is separate).

For a 5-account, 2-region setup with daily deploys: ~1 pipeline, maybe 20-40 action-minutes/deploy, so $0.04-0.08/deploy on V2 → trivial. **Cost is not the blocker.**

**Blast radius.** Self-mutating pipeline means a bad synth commit can brick the pipeline (self-mutate step fails; manual recovery via `cdk deploy` from a laptop). This is a documented risk pattern — mitigation is a healthy branch protection + PR gate. `no public post-mortem cited here, my inference from CDK Pipelines' self-mutate design`.

**Pros.** Cross-account bootstrapped trust; asset publishing to all target-account ECR/S3 is handled automatically; self-mutation removes drift between infra code and deployed pipeline.

**Cons.** CodePipeline console UX is 2015-era; iteration is slow (each change → commit → pipeline run); local `cdk deploy` still needed for prototyping.

### B.2 CDK CLI + GitHub Actions matrix

No AWS-authored blog post found in this research pass that explicitly compares. Pattern is widespread in practice.

Shape:
```yaml
strategy:
  matrix:
    account: [ml-artifact, ml-operations, ml-inference-test, ml-inference-prod]
jobs:
  deploy:
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::${{ matrix.account_id }}:role/GitHubActionsDeployRole
      - run: uv run cdk deploy --require-approval never -c target_account=${{ matrix.account }}
```

**Pros.** Iteration in seconds (workflow_dispatch or PR event); GH Actions cost is $0 within free tier for public repos, $0.008/min otherwise; no CodePipeline or artifact-bucket infra; failure isolation per matrix cell.

**Cons.** You own cross-account IAM (OIDC provider + one deploy role per account). No self-mutation — if the workflow file breaks, humans fix it. Assets larger than a few MB per file need custom upload steps.

**Cross-account trust for this path.** GitHub OIDC provider registered once per target account, plus an `AssumeRole` policy that trusts `token.actions.githubusercontent.com` filtered by repo + branch. Standard pattern; no AWS blog cited here — see https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services.

### B.3 Local `cdk deploy` only

Viable when:
- 1 developer or 1 pair
- 1-2 target accounts
- No compliance requirement for reproducible deploy provenance
- Prototype phase where the CDK app itself is changing shape weekly

Stops being viable when:
- Second engineer joins and both are deploying same stacks → drift
- Production account is in the mix → need audit trail
- Rollback story matters → need a deployed pipeline artifact history

`No specific public reference for "when does local stop working" — my inference from ml-iac repo experience and common CDK-in-anger patterns.`

### B.4 `cdk bootstrap --trust`

Source: https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html and datalake-infrastructure's `bootstrap_target_account.sh`.

Bootstrapping creates the `CDKToolkit` CFN stack with:
- S3 asset bucket
- ECR repo for Docker asset images
- IAM roles (`cdk-hnb659fds-*`): `deploy-role`, `file-publishing-role`, `image-publishing-role`, `lookup-role`, `cfn-exec-role`

Real cross-account command from datalake-infrastructure:
```bash
# In each target account:
cdk bootstrap aws://<target-account>/<region> \
  --trust <deployment-account-id> \
  --cloudformation-execution-policies arn:aws:iam::aws:policy/AdministratorAccess
```

The `--trust <account>` makes the target's `deploy-role` assumable **only** from the trusted account's principal — that's what lets a CDK Pipelines run in account A push CFN to account B.

`--trust-for-lookup` (mentioned in v2 guide) narrows to read-only lookup (for `Vpc.from_lookup` etc.) without granting deploy rights. Useful for a shared "read-only inspection" account.

**Blast-radius warning.** `AdministratorAccess` as the cfn-exec policy is the AWS-sample default but is a wide grant. Real-world hardening: scope down to just the CFN + service permissions the stacks need. `No public post-mortem cited, my inference from IAM least-privilege convention.`

### B.5 Team-shape recommendation

| Phase | Team size | Accounts | Recommend |
|---|---|---|---|
| Prototype (weeks) | 1-2 | 1-2 | Local `cdk deploy` per named profile |
| Early production (months) | 2-5 | 2-3 | GH Actions matrix with OIDC per-account roles |
| Mature (year+) | 5+ | 5+ | CDK Pipelines with self-mutation + gated prod stages |

The datalake-infrastructure repo picks CDK Pipelines because it's an AWS sample; real teams often stop at GH Actions matrix indefinitely. `Team-phase table is my inference from ml-iac evolution and the four-repo landscape reviewed — no single blog cited.`

---

## Part C — Mono vs split repo: containers + CDK

### C.1 Same-repo evidence

The `aws-samples/aws-cdk-project-structure-python` sample (https://github.com/aws-samples/aws-cdk-project-structure-python) keeps CDK code and Lambda runtime code in one repo:
- `app.py`, `toolchain.py`, `cdk.json` at root (CDK)
- `backend/<component>/runtime/` (Lambda code with its **own** `requirements.in`)
- `tests/`, `scripts/`

Two dependency scopes: root `requirements.txt` for CDK synth-time deps, `backend/*/runtime/requirements.in` for Lambda runtime deps. Separation is at the **directory + requirements-file** boundary, not the repo boundary.

Deploy path is CDK Pipelines (`toolchain.py`) — the pipeline synths the CDK and bundles the Lambda code from the same commit. **Release story: one git tag = one CDK stack + one code artifact.**

Applied to our shape:
- `cdk/` corresponds to their `app.py`/`toolchain.py`
- `containers/` corresponds to their `backend/*/runtime/`
- Container image release story: pipeline builds the image, tags it with git SHA, pushes to `ml-artifact` ECR, then CDK synth references `<repo>:<sha>` via `ecs.ContainerImage.from_ecr_repository(repo, tag=sha)`. Same commit ↔ same image ↔ same stack version.

### C.2 Split-repo evidence

AWS SDLF is effectively split-per-module: each `sdlf-*` folder is delivered as a mini-package. The whole thing is one git repo but 20+ delivery units. Consumers install per-tarball. That's not a mono/split axis so much as internal packaging.

**No blog post found in this research pass** that explicitly documents "we split infra from app repo and here's what broke." Common practitioner reasoning (`my inference from ml-iac and workflow-repo-ops experience`):

- **Why teams split.** (1) Different release cadence — infra changes rarely, app code daily. (2) Different reviewer group — SREs on IaC, engineers on app code. (3) Different CI cost profile — Docker builds are heavy, CDK synth is cheap.
- **What breaks after split.** (1) Version coordination — which image tag does the current CDK stack pin? Needs a manifest file or a parameter store lookup. (2) Cross-repo PRs — a feature that adds a new container and its scheduling requires two coordinated PRs. (3) Local dev friction — you have to run two `git pull`s.

### C.3 Trigger to split

Practical trigger from `my inference from ml-iac + ml-core split experience`:
- **Container count > 5** with independent release cycles → split.
- **Team split** (SRE team owns infra, ML team owns containers) → split.
- **CDK synth time > 2 min** → split so container image builds don't wait on it.
- Anything less than that → stay mono. Directory boundary is enough.

### C.4 Recommendation for us

**Stay mono for at least Phase 2.** Directory boundary (`cdk/` vs `containers/`) matches the AWS sample pattern. Revisit split when we hit any of the three triggers above.

---

## Part D — CDK Python idioms 2025-2026

### D.1 `pyproject.toml` structure

Ground truth is mixed. Three patterns seen:

| Pattern | Example | Notes |
|---|---|---|
| No `pyproject.toml`, use `requirements*.in` + pip-tools | aws-samples/aws-cdk-project-structure-python | Older AWS-sample style. Multiple `.in` files by dependency scope. |
| One flat `pyproject.toml` at CDK root | UFX ml-iac (current), most `cdk init --language python` scaffolds since 2023 | Simplest. One package = one dep set. |
| One `pyproject.toml` per subpackage | Monorepo tools like `uv workspace`, Nx | Only pays off past ~5 subpackages. |

**Recommendation for us.** One `pyproject.toml` at `cdk/`. If we later need a shared construct library consumed by another repo, add a second `pyproject.toml` inside `cdk/src/model_monitor_cdk/lib/` — not before. `Inference from aws-cdk-project-structure-python and ml-iac evolution.`

### D.2 `aws-cdk.assertions` test patterns

Source: https://docs.aws.amazon.com/cdk/v2/guide/testing.html (exact code quoted below).

Canonical Python test with fine-grained assertions:

```python
from aws_cdk import aws_sns as sns
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match, Capture

from app.state_machine_stack import StateMachineStack

def test_synthesizes_properly():
    app = cdk.App()
    topics_stack = cdk.Stack(app, "TopicsStack")
    topics = [sns.Topic(topics_stack, "Topic1")]
    state_machine_stack = StateMachineStack(
        app, "StateMachineStack", topics=topics
    )
    template = Template.from_stack(state_machine_stack)

    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Handler": "handler", "Runtime": "nodejs14.x"},
    )
    template.resource_count_is("AWS::SNS::Subscription", 1)
```

Matchers: `Match.object_equals` (strict), `Match.object_like` (partial), `Match.any_value`, `Match.absent`, `Match.serialized_json` (for JSON-in-string properties like SFN definitions — directly applicable to our SFN Standard state machines).

Snapshot test:
```python
def test_snapshot():
    stack = cdk.Stack()
    DeadLetterQueue(stack, "DeadLetterQueue")
    template = Template.from_stack(stack)
    assert template.to_json() == snapshot
```

**AWS explicit recommendation** (quoted from testing.html): "Fine-grained assertions are the most frequently used tests." "Snapshot tests are, for reasons we've already described, especially prone to [testing too much], so use them sparingly."

**Applied to us.** Fine-grained for every construct. Snapshot only for the top-level workflow stack where we care that "the full CFN didn't move unexpectedly." Use `Match.serialized_json` to assert SFN Parallel-branch retry/catch structure without regexing raw JSON.

### D.3 Env-specific values: context vs env vars vs .env

Real-world spread:
- `cdk.json` context — canonical AWS-sample default. Cached in `cdk.context.json` after first synth. Brittle for account IDs because commits carry the cache file.
- Python config module — datalake-infrastructure's approach (`lib/configuration.py`). Everything committed except secrets. Easiest to reason about; grep-able.
- Env vars at synth-time — pattern used by GH Actions matrix deploys (`-c target_account=$MATRIX_ACCOUNT`).
- `.env` files — informal; not a CDK primitive. Loaded by `python-dotenv` in `app.py` before `App()` construction.

**Recommendation.** Config module (`cdk/src/model_monitor_cdk/config.py`) with a dict-of-envs. Never `cdk.json` context for account IDs — commit-cache surprises. Env var `TARGET_ACCOUNT` selects which env dict to use at synth time (matches ml-iac's `-c target_account=...` pattern). `Inference from datalake-infrastructure config module + ml-iac convention.`

### D.4 Snapshot vs fine-grained

AWS testing.html explicitly recommends fine-grained as the default; snapshot for refactor-safety only. `Direct quote referenced above.`

---

## Part E — Naming conventions

### E.1 Stack names

Real conventions from public repos:

| Repo | Pattern | Example |
|---|---|---|
| datalake-infrastructure | `<Env><LogicalPrefix><Component>` | `DevDataLakeCDKBlogInfrastructureVpc` |
| mlops-workload-orchestrator | `<ProductName>Stack` | `mlops_orchestrator_stack.py` → `MlopsOrchestratorStack` |
| aws-cdk-project-structure-python | `<Product><Env>` | `UserManagementBackendSandbox`, `UserManagementBackendProduction` |

Common thread: **PascalCase, env + component**. Nobody uses lowercase. Nobody hardcodes account IDs into stack names.

### E.2 Physical resource names

Two schools:
1. **Leave to CDK autogen** (default, recommended by AWS testing.html implicitly — the sample stack never sets `bucket_name`). Autogen names collision-free across stacks and across accounts.
2. **Explicit for cross-account refs.** datalake-infrastructure uses `RESOURCE_NAME_PREFIX = 'cdkblog-e2e'` for globally-unique buckets. Explicit only when another account has to name the resource by string.

Applied to us: autogen everything except the ECR repo (needs stable name for container tagging) and the state machine (needed by cron trigger from another account).

### E.3 Tagging enforcement

Source: https://docs.aws.amazon.com/cdk/v2/guide/tagging.html.

Direct quote: "Tagging is implemented using Aspects and the AWS CDK. Aspects are a way to apply an operation (such as tagging) to all constructs in a given scope."

So `Tags.of(scope).add(key, value)` **is already an Aspect** under the hood — the tree-walking is automatic. You don't need to write your own IAspect for standard tagging. You'd only write one when tag values depend on per-construct properties (the `PathTagger` example in the docs).

Canonical Python:
```python
from aws_cdk import App, Tags
app = App()
Tags.of(app).add("Project", "model-monitor-custom")
Tags.of(app).add("Environment", target_env)  # dev/test/prod
Tags.of(app).add("Owner", "ml-platform")
Tags.of(app).add("CostCenter", "ml-ops")
Tags.of(app).add("Component", stack_component)  # per-stack override
```

**Precedence rules** (from same doc): default priority 100 for add, 200 for remove, 50 for tags added directly to a CFN resource. Apply broad tags at `App` level, narrower overrides at `Stack` level — the deeper add wins by tree distance if priorities are equal.

**`Stage` boundary caveat** (direct quote): "If you are using `Stage` constructs, apply the tag at the `Stage` level or below. Tags are not applied across `Stage` boundaries." Matters when we use `pipelines.CodePipeline` with `Stage`s — App-level tags won't propagate; put them on each `Stage`.

---

## Part F — Concrete recommendation for `model-monitor-custom`

### F.1 Stack layout

Given the runtime (SFN Standard + ECS Fargate + two workflows + three account roles), the stack cut:

| # | Stack | Account | Contents |
|---|---|---|---|
| 1 | `ArtifactStack` | `ml-artifact` | ECR repos (one per container: monitor, baseline, shared), model-registry references (`MPG` names), cross-account resource policies letting `ml-operations` + `ml-inference-*` pull images |
| 2 | `OperationsBaselineStack` | `ml-operations` | Baseline-compute ECS cluster (or reuse existing), baseline-SFN state machine, S3 baseline-output bucket, IAM roles for the baseline task |
| 3 | `InferenceMonitorStack` | each `ml-inference-*` | Snapshot workflow SFN (S3-triggered) + live workflow SFN (cron-triggered), ECS Fargate task definitions, EventBridge rule (cron + S3 event), CW dashboards, DDB outcome table |
| 4 | `SharedIamStack` | `ml-artifact` | Cross-account role stubs referenced by other stacks — never hardcodes the consumer's account, takes it as prop |
| 5 | (later) `PipelineStack` | deployment account | CDK Pipelines definition once we hit the mature-team phase |

Stacks 1-4 are Phase-2 required. Stack 5 is Phase-3.

**Threading account IDs.** Every stack constructor takes a `Config` prop with `artifact_account_id`, `operations_account_id`, `inference_account_ids: dict[env, id]`, `region`. `app.py` reads env var `TARGET_ACCOUNT` and picks the right dict — same pattern as ml-iac.

### F.2 Deploy tool for prototype

**Pick: local `cdk deploy` per named profile.**

Reasoning:
- Team size 1-2 for Phase 2.
- Two target accounts initially (`ml-artifact` + `ml-inference-test`).
- CDK app shape will change weekly.
- CodePipeline cost is trivial ($1/mo) but iteration lag is not.

Migration trigger: when a second engineer joins Phase 3, or when we deploy into `ml-inference-prod`. At that point add a GitHub Actions matrix workflow with OIDC roles (skip CDK Pipelines — self-mutate blast radius isn't worth it until team-size 5+).

### F.3 Repo shape

**Stay mono.** `containers/` + `cdk/` in one tree. Directory boundary + separate dep files.

Split trigger: any of container-count > 5, team split, synth > 2 min. `Inference from Part C reasoning.`

### F.4 `cdk/pyproject.toml` structure

One file at `cdk/pyproject.toml`. Sample skeleton:

```toml
[project]
name = "model-monitor-cdk"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "aws-cdk-lib>=2.150.0",
    "constructs>=10.3.0",
    "pydantic>=2.7",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "aws-cdk-lib",  # assertions module comes with it
    "pyright",
    "ruff",
]

[tool.setuptools.packages.find]
where = ["src"]
```

Layout under `cdk/`:
```
cdk/
  pyproject.toml
  cdk.json                # only app + featureFlags, NOT account IDs
  app.py
  src/model_monitor_cdk/
    config.py             # per-env dicts (account IDs, region, tag values)
    stacks/
      artifact_stack.py
      operations_baseline_stack.py
      inference_monitor_stack.py
      shared_iam_stack.py
    constructs/
      analyser_task.py    # reusable ECS Fargate task-def construct
      workflow_sfn.py     # snapshot + live workflow builders
      cross_account_role.py
    aspects/
      tagging.py          # only if we need value-per-construct tags
  tests/
    unit/
      test_artifact_stack.py
      test_inference_monitor_stack.py
```

Matches mlops-workload-orchestrator's `constructs/` (`pipeline_definitions/`) vs `stacks/` (`ml_pipelines/`) split.

### F.5 Naming convention

- **Stacks:** `MMC-<Env>-<Component>`. `MMC` = model-monitor-custom short. Env one of `dev|test|prod`. Component one of `Artifact|OpsBaseline|InferenceMonitor|SharedIam`. Example: `MMC-test-InferenceMonitor`. Pascal-case-with-dashes matches CFN stack-name legal charset and stays grep-friendly.
- **Physical resource names:** autogen for everything except (a) ECR repos: `mmc/<container>` (e.g. `mmc/monitor`, `mmc/baseline`), (b) SFN state machines: `mmc-<env>-<workflow>-sfn`, (c) EventBridge cron rule: `mmc-<env>-live-cron`.
- **Construct IDs:** PascalCase describing role, not resource type. `BaselineTaskDef` not `BaselineFargateTaskDefinition`.

### F.6 Tagging strategy

App-level `Tags.of(app).add(...)` at the top of `app.py` before any stack instantiation:

| Tag | Value | Purpose |
|---|---|---|
| `Project` | `model-monitor-custom` | Cost allocation |
| `Environment` | `dev`/`test`/`prod` | Env filter |
| `Owner` | `ml-platform` | Contact / on-call |
| `CostCenter` | `ml-ops` | Finance |
| `Component` | overridden per stack | Component filter |
| `ManagedBy` | `cdk` | Distinguishes from click-ops |

`Component` is set per-stack via `Tags.of(stack).add("Component", "InferenceMonitor")` overriding the default (higher tree depth wins at same priority — direct from tagging.html precedence rules).

**When we adopt `Stage` (CDK Pipelines phase):** re-apply the App-level tags at the `Stage` level too, per the docs quote on Stage boundaries.

### F.7 First 3 stacks to build in Phase 2 (before container code)

Order matters: give containers targets to deploy against before writing container code.

1. **`ArtifactStack` in `ml-artifact`** — ECR repos empty but named. Cross-account pull policies open to `ml-operations` and `ml-inference-*` account IDs. This unblocks container image push in the CI pipeline that follows.
2. **`SharedIamStack` in `ml-artifact`** — Cross-account role definitions the other accounts assume for read/list on the artifact bucket + ECR. Named roles so downstream stacks can reference by ARN without needing a lookup at synth time.
3. **`InferenceMonitorStack` skeleton in `ml-inference-test`** — SFN state machines with placeholder ECS task-defs that reference the ECR repos from (1) via `:latest` tag. State machines fully wired (Parallel + Retry + Catch) but the container image can be a `busybox` sleep-and-exit for a first synth. This lets us `cdk deploy` the workflow shape and start writing SFN assertions against the synthesised template before real container code exists.

Once (3) is deployed with dummy images, container work in `containers/monitor/` can iterate against a real ECR + real SFN + real EventBridge trigger. `Ordering is my inference from ml-iac Phase-2 pattern — no single blog cited.`

---

## Source ledger

| # | URL | Used for |
|---|---|---|
| 1 | https://github.com/awslabs/aws-serverless-data-lake-framework | SDLF module structure (Part A.2), delivery-by-tarball |
| 2 | https://github.com/aws-solutions/mlops-workload-orchestrator | CDK Python stacks vs constructs split, StackSets cross-account (Part A.3) |
| 3 | https://github.com/aws-samples/aws-cdk-pipelines-datalake-infrastructure | Mid-grained stacks + config-as-Python-dict + CDK Pipelines + bootstrap trust script (Parts A.1, B.1, B.4, E.1) |
| 4 | https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html | Bootstrap resources + `--trust` model (Part B.4) |
| 5 | https://docs.aws.amazon.com/cdk/v2/guide/testing.html | Fine-grained vs snapshot direct quotes + Python assertions example (Part D.2, D.4) |
| 6 | https://docs.aws.amazon.com/cdk/v2/guide/tagging.html | Tags.of() semantics, Aspects underpinning, Stage boundary quote (Part E.3) |
| 7 | https://docs.aws.amazon.com/prescriptive-guidance/latest/best-practices-cdk-typescript-iac/organizing-code-best-practices.html | `common/` folder factory pattern (Part D.1) |
| 8 | https://aws.amazon.com/codepipeline/pricing/ | V1 $1/pipeline/mo, V2 $0.002/action-min, free tier (Part B.1) |
| 9 | https://github.com/aws-samples/aws-cdk-project-structure-python | Same-repo Lambda + CDK layout, two-scope requirements files (Part C.1, D.1) |
| 10 | https://github.com/aws-samples/aws-cdk-examples/tree/master/python | Cross-account examples snippet library (Part A.4) |

Deliverable path: `/Users/eoinmca/UFX/model-monitor-custom/docs/IAC_DESIGN_RESEARCH.md`
