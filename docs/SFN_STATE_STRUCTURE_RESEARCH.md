# SFN State Structure Research — Pre/Post the Parallel State

**Question:** For a Standard Step Functions workflow that fans out to N containerised analyser jobs on ECS Fargate (`ecs:runTask.sync`), what states and tasks appear **before** and **after** the `Parallel` state in real production reference architectures? Specifically: is a "Prepare" Lambda (config + per-branch arg resolution) and a "Publish" Lambda (aggregate + write CW metrics + DDB) the right shape, or do teams do it differently?

**Method:** Fanned out over public GitHub code search + AWS official docs. Every claim below is anchored to a specific URL. Where no public reference exists for a specific claim, that is stated explicitly.

**Date:** 2026-07-02.

---

## Part A — Real reference architectures

Five concrete public repositories with production-shaped ASL definitions were examined. All use `ecs:runTask.sync` or `.waitForTaskToken` from either a `Parallel` or `Map` state.

### A.1 `hosimesi/aws-mlops-practice` — ML training pipeline, static Parallel with Fargate branches

**URL:** https://github.com/hosimesi/aws-mlops-practice/blob/0b8d6dccffe1557703b9e9132a8f308930dfec6f/infra/modules/stepfunctions/definition/execute_ml_pipeline_parallel.json

This is the closest match to our workload — a **static Parallel state fanning out to per-model Fargate training tasks**, where each branch is a specific ML model.

**Shape:** `StartAt: "Parallel Training"` → `Parallel` with N hard-coded branches → `Update Server` (a follow-up Fargate task).

**No prepare Lambda.** No preceding state at all — the Parallel state is `StartAt`. Per-branch config (model name) is baked directly into the ASL as a static `Environment` override:

```json
"Overrides": {
  "ContainerOverrides": [
    {
      "Name": "ml-pipeline",
      "Environment": [
        { "Name": "MODEL", "Value": "sgd_classifier_ctr_model" }
      ]
    }
  ]
}
```

**No aggregator Lambda.** After the Parallel state, control transitions directly to a follow-up `ecs:runTask` (a "deployment / update server" step). The branch outputs are not aggregated or written anywhere by the state machine — the training containers themselves are assumed to have persisted their artefacts to S3.

**Key takeaway:** small-fan-out, static, self-writing branches. No orchestrator glue. This is the "everything in-container" pattern.

### A.2 `aws-samples/serverless-patterns` (`sfn-ecs-sam`) — Map + `.waitForTaskToken` fan-out to Fargate

**URL:** https://github.com/aws-samples/serverless-patterns/blob/93a656bf9a226c93860344574d0ff6b846973494/sfn-ecs-sam/src/statemachine/sfn-ecs-datapipeline.asl.json

**Shape:** `StartAt: "Parallel ECS Tasks"` (a `Map`, misleadingly named "Parallel") → `Review Results` (a bare `Pass` state).

Uses `runTask.waitForTaskToken` with the token injected into the container env:

```json
"Environment": [
  { "Name": "TASK_TOKEN", "Value.$": "$$.Task.Token" },
  { "Name": "S3_BUCKET",  "Value.$": "$" }
]
```

**No prepare Lambda.** Input `$.bucketArray` is expected to be pre-shaped by the caller of the state machine.
**No aggregator Lambda.** `Review Results` is a `Pass` state — literally a no-op. Each branch does its own work (implied: writing to S3) and calls `SendTaskSuccess` from inside the container to unblock the state machine.

**Key takeaway:** AWS's own sample explicitly demonstrates the "container-drives-completion via task token, orchestrator does no aggregation" pattern.

### A.3 `aws-solutions-library-samples/data-lakes-on-aws` — SDLF ECS Fargate stage

**URL:** https://github.com/aws-solutions-library-samples/data-lakes-on-aws/blob/8e0fa942673f1ab8175ba38d1e6a8cf142040805/sdlf-stage-ecsfargate/src/state-machine/stage-ecsfargate.asl.json

This is the most sophisticated shape and the closest to a "publish"-style pattern. Structure:

```
"Try" (Parallel) → catches all errors → "Error" (Lambda) → "Fail"
  Branch:
    Pass (JSON parse)
      → Map (Distributed, ExecutionType=STANDARD)
          each item → ecs:runTask.sync (transform in Fargate)
      → Post-update Catalog (Lambda)
```

Key observations:
- **The `Parallel` state here is used as an error boundary, not a fan-out.** It has a single branch. The actual fan-out is the inner `Map`.
- **A post-fan-out Lambda exists** — `Post-update Catalog` invokes `${lPostMetadata}:$LATEST` after all Map iterations complete. This is the "publish" role: writing metadata (in this case, glue catalogue updates) after workers finish.
- **A separate error-handler Lambda** (`${lError}`) is invoked from the outer Parallel's `Catch`.
- **No prepare Lambda before.** The `Pass` state just calls `States.StringToJson($)` — pure transformation, no external state fetched.

Verbatim:

```json
"Post-update Catalog": {
  "Type": "Task",
  "Resource": "arn:aws:states:::lambda:invoke",
  "ResultPath": null,
  "Parameters": {
    "Payload.$": "$",
    "FunctionName": "${lPostMetadata}:$LATEST"
  },
  ...
  "End": true
}
```

**Key takeaway:** AWS Solutions team ships a real "post-processor Lambda" for catalogue/metadata write-back. They do **not** ship a prepare Lambda — they use a `Pass` with intrinsic functions for the input transformation.

### A.4 `toricls/aws-fargate-with-step-functions` — parallelised Fargate with SNS notification pre/post

**URL:** https://github.com/toricls/aws-fargate-with-step-functions/blob/72119f20bfada7020719f8298cc436e302ed3f64/2-parallelized-fargate-tasks/template.yml

**Shape:**

```
Process input (Map, MaxConcurrency 3)
  each iterator: Run Fargate Task (ecs:runTask.sync)
                 Catch → Notify If Task Failed (sns:publish)
  Catch → Notify Failure (sns:publish)
→ Notify Success (sns:publish)
```

**No prepare Lambda.** Input `$.data` is expected pre-shaped; `Parameters` extracts index + value from the Map context object:

```json
"Parameters": {
  "myIndex.$": "$$.Map.Item.Index",
  "myValue.$": "$$.Map.Item.Value"
}
```

**No Lambda after** — but there **is** post-fan-out work via a **direct SDK service integration** (`arn:aws:states:::sns:publish`). No Lambda glue.

**Key takeaway:** the "publish results" role is fulfilled by a **direct AWS SDK integration** (`sns:publish`), not a Lambda. Same idea, no code.

### A.5 `VerticalRelevance/Experiment-Broker` — payload processor with Map + `.sync` + task token

**URL:** https://github.com/VerticalRelevance/Experiment-Broker/blob/8153893e686f4ef45943554bd51fe0ad080bf639/deployments/terraform/experiment_broker/modules/state_machine_ecs/state_machine_ecs.json

**Shape:** `Choice (state=pending) → Choice (parallel_enabled) → Map → ecs:runTask.sync per item → Choice → Pass/Wait/Fail`

**No dedicated prepare Lambda.** Preceding states are **`Choice` states** that branch on already-present input fields (`$.Payload.state`, `$.Payload.parallel_enabled`).

**No aggregator Lambda.** Downstream of the Map is a `Pass`/`Fail`/`Wait` chain based on per-item output. Each branch pushes `task_token`, `bucket_name`, `output_bucket`, `output_path` into the container so the container writes its own outputs and calls back with `SendTaskSuccess`.

Verbatim envelope pattern:

```json
"Environment": [
  { "Name": "task_token",         "Value.$": "$$.Task.Token" },
  { "Name": "bucket_name",        "Value.$": "$.bucket_name" },
  { "Name": "experiment_source",  "Value.$": "$.experiment_source" },
  { "Name": "output_bucket",      "Value.$": "$.output_bucket" },
  { "Name": "output_path",        "Value.$": "$.output_path" }
]
```

**Key takeaway:** container-writes-own-output via task token is the dominant pattern when per-branch outputs are heterogeneous. Pre/post state machine logic is `Choice`/`Pass`, not Lambda.

### A.6 (Cross-reference) `markymarkus/cloudformation/step-functions-parallel-fargate`

**URL:** https://github.com/markymarkus/cloudformation/blob/0c2e64a948d58fb1b312f6e47694eecd2fb5a0b6/step-functions-parallel-fargate/cfn/stepfunctions-fargate.yaml

Same shape: `Pass` (no-op named "first") → `Map` fanning out to Fargate → `End`. **Zero pre-processing state** beyond the placeholder `Pass`. No post-processing state at all.

---

## Part B — AWS-official guidance

### B.1 AWS Step Functions Best Practices doc

**URL:** https://docs.aws.amazon.com/step-functions/latest/dg/sfn-best-practices.html

Explicit guidance found:

- **Large payloads:** "If the data you are passing between states might grow to over 256 KiB, use Amazon Simple Storage Service (Amazon S3) to store the data, and parse the Amazon Resource Name (ARN) of the bucket in the `Payload` parameter to get the bucket name and key value." This is the canonical justification for "container reads/writes S3 itself" — the state machine should not thread large payloads through.
- **No prescription** about a "prepare" or "aggregate" state. The doc does not mention `Parallel` structuring at all — the topics are cost (Standard vs Express), timeouts, history quota, Lambda retry, activity poller latency, and CloudWatch resource policies.

**Direct quote:**
> "Executions that pass large payloads of data between states can be terminated. If the data you are passing between states might grow to over 256 KiB, use Amazon S3 to store the data ..."

Implication: AWS's stated best practice is to **thread S3 URIs, not payloads**, between states. That biases toward containers reading their own inputs and writing their own outputs — because putting a Lambda in the middle to aggregate branch results only works if those results are small.

### B.2 Parallel state reference

**URL:** https://docs.aws.amazon.com/step-functions/latest/dg/amazon-states-language-parallel-state.html

Key behavioural points (verbatim):

- "A `Parallel` state provides each branch with a copy of its own input data (subject to modification by the `InputPath` field). It generates output that is an array with one element for each branch, containing the output from that branch."
- "If any branch fails, because of an unhandled error or by transitioning to a `Fail` state, the entire `Parallel` state is considered to have failed and all its branches are stopped."
- "When a parallel state fails, invoked Lambda functions continue to run and activity workers processing a task token are not stopped."

This tells us three things relevant to structuring pre/post the Parallel:

1. Each branch gets a **copy of the whole state input**. If per-branch config differs, either (a) each branch's ASL filters via `InputPath`/`Parameters`, or (b) something upstream shapes the input into an array and the state machine uses `Map`, not `Parallel`.
2. Branch outputs are aggregated **into a JSON array automatically** — no aggregator Lambda is required to combine them. A downstream state receives `[branch0_output, branch1_output, ...]`.
3. Failure is all-or-nothing at the Parallel-state boundary. Retries and Catches on the Parallel state apply to the whole set, not per-branch. Per-branch retry/catch must be defined **inside each branch**.

### B.3 Parameters / Paths reference

**URL:** https://docs.aws.amazon.com/step-functions/latest/dg/connect-parameters.html

AWS's documented mechanisms for per-branch parameterisation, in order of complexity:

1. **Static JSON** — hardcoded in the ASL (`hosimesi` pattern).
2. **JSONPath from state input** — `"Value.$": "$.input.message"`.
3. **Context object** — `$$.Task.Token`, `$$.Map.Item.Index`, `$$.Map.Item.Value`, `$$.Execution.Name`.

The doc does **not** suggest a preparatory Lambda for parameterisation. The recommended pattern is: shape the input at execution-start time (either by the caller, or by a `Pass` state with intrinsic JSONata / JSONPath), then pass paths into `Parameters`.

### B.4 Search for phrases in AWS docs / blogs

- **"prepare state"** — no AWS documentation hit found for this as a named pattern.
- **"aggregate results"** — appears in Distributed Map / `ResultWriter` context, not in Parallel context.
- **"fan-in"** — not present as a named pattern in the Step Functions dev guide indexed pages fetched. The Parallel state page describes the fan-in *behaviour* (output-as-array) but does not use the term "fan-in".
- **"direct service integration"** — the entire `connect-*.md` family of pages documents this (see e.g. https://docs.aws.amazon.com/step-functions/latest/dg/connect-parameters.html and adjacent pages). AWS's own tone is that direct integrations are preferred over Lambda glue where a suitable SDK integration exists — this is not a verbatim quote but is consistent across the docs and matches example A.4 (SNS publish directly, no Lambda).

*No public reference explicitly ranks "Prepare Lambda + Parallel + Publish Lambda" vs alternatives. That framing is not an AWS-published pattern name.*

---

## Part C — Pattern catalogue

Four observed patterns, ranked by prevalence in the sampled references:

### Pattern 1: Everything in-container (container reads config, writes outputs)

**How common in sample:** 3 of 5 direct hits (A.1 hosimesi, A.2 aws-samples/serverless-patterns, A.5 VerticalRelevance).

**Shape:**
```
[Choice/Pass at most]
   ↓
Parallel or Map
   ↓ each branch: ecs:runTask.sync with env vars threaded from input
   ↓ (container reads SSM/S3, writes S3/DDB itself)
[Pass or nothing]
```

**Trade-offs teams call out:**
- Pro: minimal orchestration surface; state machine stays under the 256KiB payload limit ([best-practices doc](https://docs.aws.amazon.com/step-functions/latest/dg/sfn-best-practices.html))
- Pro: each branch is independently retryable via its own inner `Retry`/`Catch` (see A.4 template.yml — retry `States.TaskFailed` + `ECS.AmazonECSException`)
- Con: no single place to see "what did each branch produce" — outcomes are scattered across S3/DDB writes done by container code

### Pattern 2: Direct AWS SDK integration for post-work (no Lambda glue)

**How common in sample:** 1 of 5 (A.4 toricls) — but consistent with AWS docs' general preference for SDK integrations over Lambda glue.

**Shape:**
```
Map/Parallel
   ↓
sns:publish  OR  dynamodb:putItem  OR  cloudwatch:putMetricData  (direct SDK integration)
```

**Trade-offs:**
- Pro: no code, no cold-start, no IAM sprawl beyond the state machine role
- Con: limited transformation ability; only works when the aggregated output shape maps cleanly to a single API call. `cloudwatch:putMetricData` in particular takes only up to 1000 metrics per call — a fan-in of 9 branches each producing 5 metrics (45 total) fits fine.

*Note: `arn:aws:states:::aws-sdk:cloudwatch:putMetricData` is documented as an [AWS SDK service integration](https://docs.aws.amazon.com/step-functions/latest/dg/supported-services-awssdk.html). I did not find a public GitHub example combining Parallel-Fargate fan-out with a direct `putMetricData` fan-in — this is inference from AWS's own service-integration catalogue, not a copied production pattern.*

### Pattern 3: Prepare Lambda + Parallel + Publish Lambda (the shape you asked about)

**How common in sample:** 0 of 5 exact matches. **The closest hit is A.3 SDLF** which ships a *post-fan-out Lambda* (`Post-update Catalog`) but **no prepare Lambda** — that role is filled by a `Pass` state with `States.StringToJson($)`.

**No public reference found for the symmetric "prepare Lambda + parallel + publish Lambda" as a named pattern.** This is my inference: teams either (a) shape input upstream with `Pass` + intrinsics, (b) shape it in the state machine's caller, or (c) hardcode per-branch config in the ASL.

**When it would actually pay off:**
- If per-branch config must be resolved dynamically at execution time (e.g. read from SSM Parameter Store) AND depends on execution input in a non-trivial way that JSONata/JSONPath cannot express.
- If aggregated branch outputs require enrichment before being written to a sink (e.g. joining branch results with external metadata).

**Trade-offs:**
- Pro: single place for orchestration logic; testable in isolation
- Con: extra Lambda in the critical path (cost, cold start, another IAM role, another CloudWatch log group)
- Con: another moving piece for each container change to keep in sync

### Pattern 4: Prepare Lambda + Parallel, no Publish (branches write their own outputs)

**How common in sample:** 0 of 5 exact matches, but this is the natural intermediate shape.

**When it fits:** input shaping is genuinely non-trivial (justifying the Lambda) but per-branch outputs go to heterogeneous sinks (CW metrics for live, S3 for snapshot) that don't aggregate cleanly.

---

## Part D — Recommendation for our shape

**Our shape (from the brief):**
- 4 snapshot analyser branches + 5 live analyser branches
- Each branch reads: SSM config, S3 input, optional model artefact from S3
- Each branch writes: S3 output (snapshot) OR CW Metrics + DDB (live)
- Requirements: each branch independently retryable + failable; fail atomically **or** track per-branch outcomes

### Concrete state machine structure

Recommend **Pattern 1 (everything in-container) with a small enrichment via `Pass` and a direct-SDK fan-in for the aggregate metric.**

```
StartAt: "Shape input"
States:
  "Shape input":                           # Pattern from A.3 (SDLF) — Pass + intrinsics, no Lambda
    Type: Pass
    Parameters:                            # or use JSONata if preferred
      runId.$: "$$.Execution.Name"
      snapshotBranches: [...static...]
      liveBranches: [...static...]
      input.$: "$"
    Next: "Fan out analysers"

  "Fan out analysers":                     # Pattern from A.1 (hosimesi) — static Parallel with per-branch env
    Type: Parallel
    Branches:
      - # snapshot-1 ... snapshot-4  (4 branches)
        StartAt: "run snapshot-1"
        States:
          "run snapshot-1":
            Type: Task
            Resource: "arn:aws:states:::ecs:runTask.sync"
            Parameters:
              LaunchType: FARGATE
              Cluster: ${cluster}
              TaskDefinition: ${taskDef}
              Overrides:
                ContainerOverrides:
                  - Name: analyser
                    Environment:
                      - Name: ANALYSER
                        Value: snapshot-1
                      - Name: RUN_ID
                        Value.$: "$.runId"
                      - Name: SSM_PREFIX
                        Value: /monitor/snapshot-1
            Retry: [ ... per-branch retry on ECS.AmazonECSException + States.TaskFailed ... ]  # from A.4
            Catch: [ ... route to per-branch failure marker if you want per-branch tracking ... ]
            End: true
      - # live-1 ... live-5  (5 branches) — same shape, different env vars
    ResultPath: "$.branchResults"
    Next: "Publish run summary"

  "Publish run summary":                   # Pattern from A.4 — direct SDK integration, no Lambda
    Type: Task
    Resource: "arn:aws:states:::aws-sdk:cloudwatch:putMetricData"
    Parameters:
      Namespace: "UfxMonitor"
      MetricData:
        - MetricName: "RunCompleted"
          Value: 1
          Dimensions: [{Name: RunId, Value.$: "$.runId"}]
    ResultPath: null
    End: true
```

### Why this over "Prepare Lambda + Parallel + Publish Lambda"

1. **Config resolution belongs in the container, not a Lambda.** SSM Parameter Store fetch is one boto3 call. The analyser already needs boto3 for S3 reads. Adding a Lambda upstream to fetch SSM and thread values through the state machine input just moves that same call earlier and multiplies IAM roles.

2. **Per-branch outputs are heterogeneous** (S3 vs CW+DDB). An aggregator Lambda has nothing coherent to aggregate — each branch writes to its own sink using its own schema. A "Publish Lambda" that fans out to different sinks per branch type is just re-encoding the branch fan-out in Lambda code — worse than the SFN native fan-out.

3. **Atomic-fail is free.** The Parallel state's default behaviour is atomic fail (A.3 `Try` → outer `Catch` demonstrates this). Combined with per-branch `Retry`/`Catch` inside each branch (A.4 shows the exact retry list for ECS: `States.TaskFailed` + `ECS.AmazonECSException`), you get both independent retryability **and** all-or-nothing final outcome — no Lambda needed.

4. **Per-branch outcome tracking is free.** Each branch's `ecs:runTask.sync` return value carries `Containers[*].ExitCode`, `LastStatus`, `StoppedReason` — see A.4's `"$.Containers[?(@.Name=='fargate-app')].ExitCode"` extraction. That flows into `$.branchResults` automatically. If you need per-branch outcome persisted to DDB, add a **single** downstream `arn:aws:states:::aws-sdk:dynamodb:batchWriteItem` or use a small `Map` over `$.branchResults` — still no Lambda.

5. **The one AWS-shipped real reference that has a "publish" step (A.3 SDLF) uses it for cross-cutting metadata (Glue catalogue), not for aggregating branch outputs.** That is a legitimate reason to add a Lambda; ours is not.

### Which reference to mirror

**Primary mirror: A.1 `hosimesi/aws-mlops-practice`** for the static-Parallel + per-branch env-var-override shape. Direct match to "N heterogeneous analyser containers, config is static per branch, orchestrator does nothing clever."

**Secondary mirror: A.4 `toricls/aws-fargate-with-step-functions`** for:
- Per-branch `Retry` on `States.TaskFailed` + `ECS.AmazonECSException`
- Per-branch `Catch` routing to a failure-marker state
- Direct SDK integration (`sns:publish` in their case, `cloudwatch:putMetricData` in ours) for the tail step

**Tertiary borrow: A.3 SDLF** for the `Pass` + intrinsics pattern to shape input **only if** the caller cannot pre-shape it.

### When to reconsider

Add a Prepare Lambda **only when** any of these become true:
- Per-branch config depends on runtime lookups that JSONata/JSONPath cannot express (multi-hop joins, DB queries).
- The set of branches is dynamic per execution — but then you should switch from `Parallel` to a Distributed `Map` state anyway (A.3 uses Distributed Map), not add a Lambda.

Add a Publish Lambda **only when** the aggregation logic across branches is non-trivial (e.g. computing a derived signal from all branch outputs, dedup across DDB writes, cross-branch invariant checks). None of those are in the current requirement set.

---

## Citations index

| # | Source | URL |
|---|---|---|
| A.1 | hosimesi ML pipeline parallel ASL | https://github.com/hosimesi/aws-mlops-practice/blob/0b8d6dccffe1557703b9e9132a8f308930dfec6f/infra/modules/stepfunctions/definition/execute_ml_pipeline_parallel.json |
| A.2 | aws-samples serverless-patterns sfn-ecs-sam | https://github.com/aws-samples/serverless-patterns/blob/93a656bf9a226c93860344574d0ff6b846973494/sfn-ecs-sam/src/statemachine/sfn-ecs-datapipeline.asl.json |
| A.3 | aws-solutions-library-samples SDLF ecsfargate | https://github.com/aws-solutions-library-samples/data-lakes-on-aws/blob/8e0fa942673f1ab8175ba38d1e6a8cf142040805/sdlf-stage-ecsfargate/src/state-machine/stage-ecsfargate.asl.json |
| A.4 | toricls parallelised Fargate w/ SFN | https://github.com/toricls/aws-fargate-with-step-functions/blob/72119f20bfada7020719f8298cc436e302ed3f64/2-parallelized-fargate-tasks/template.yml |
| A.5 | VerticalRelevance Experiment-Broker | https://github.com/VerticalRelevance/Experiment-Broker/blob/8153893e686f4ef45943554bd51fe0ad080bf639/deployments/terraform/experiment_broker/modules/state_machine_ecs/state_machine_ecs.json |
| A.6 | markymarkus parallel-fargate cfn | https://github.com/markymarkus/cloudformation/blob/0c2e64a948d58fb1b312f6e47694eecd2fb5a0b6/step-functions-parallel-fargate/cfn/stepfunctions-fargate.yaml |
| B.1 | SFN best-practices doc | https://docs.aws.amazon.com/step-functions/latest/dg/sfn-best-practices.html |
| B.2 | Parallel state ASL reference | https://docs.aws.amazon.com/step-functions/latest/dg/amazon-states-language-parallel-state.html |
| B.3 | Passing parameters to service API | https://docs.aws.amazon.com/step-functions/latest/dg/connect-parameters.html |
| B.4 | AWS SDK service integrations catalogue | https://docs.aws.amazon.com/step-functions/latest/dg/supported-services-awssdk.html |
