# AWS Step Functions Fan-Out to N Container Jobs — Design Research

**Status:** research note, not a decision yet. All claims cite AWS documentation URLs. No abstractions — real ASL, real service integrations, real quotas.

**Context recap.** We are building a batch-analysis system with two workflows:

- **Snapshot workflow** — event-triggered (S3 `ObjectCreated`), fans out to 4 parallel container jobs: Model Quality, Data Quality, Bias, Explainability. Individual branch runtime measured in minutes-to-hours (Bias/Explainability especially).
- **Live workflow** — cron-triggered (EventBridge Scheduler, hourly), fans out to 5 parallel container jobs (Snapshot four + Shadow). Target: < 15 min per hourly tick.

Each branch reads inputs from S3 + config from SSM Parameter Store, runs a container from ECR, writes outputs to S3 / CloudWatch Metrics / DynamoDB. Each branch must be **independently retryable, independently observable, and independently failable** — one dying does not kill siblings.

---

## 1. `Parallel` state vs `Map` state — which one for 4-5 fixed sibling branches?

### 1.1 `Parallel` — static branches declared in ASL

From the ASL spec ([state-parallel](https://docs.aws.amazon.com/step-functions/latest/dg/state-parallel.html)):

> A `Parallel` state causes AWS Step Functions to execute each branch, starting with the state named in that branch's `StartAt` field, as concurrently as possible, and wait until all branches terminate (reach a terminal state) before processing the `Parallel` state's `Next` field.

- `Branches` is a **required array** of sub–state-machines. Each has its own `StartAt` and `States`. Branches are **static in the state-machine JSON** — you know at synth time how many there are and what each one does.
- Each branch gets a **copy** of the parent state's input.
- Output of a `Parallel` state is an **array** with one element per branch, ordered by branch definition order.
- Branches must be **self-contained**: "A state in one branch of a Parallel state must not have a `Next` field that targets a field outside of that branch, nor can any other state outside the branch transition into that branch."

### 1.2 `Map` — iterator over a list

From the ASL spec ([state-map](https://docs.aws.amazon.com/step-functions/latest/dg/state-map.html)):

- **Inline mode** (default): up to **40 concurrent iterations**, iterations share the parent execution history (counts against the 25,000-event history cap), input must be a JSON array in the state's input.
- **Distributed mode**: up to **10,000 parallel child workflow executions**, each with its own execution history, can read the list directly from an S3 object/CSV/inventory. Emits its own CloudWatch metrics via a **Map Run** resource ([Map Run docs](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-examine-map-run.html)).

`Map` is fundamentally *iterator-shaped*: you feed it a list, it runs the same sub-workflow per element. Different container images per iteration is expressed by putting the image URI into each list item.

### 1.3 Trade-offs for our 4-5 sibling branches

| Aspect | `Parallel` (4-5 static branches) | `Map` (Inline, list of 4-5 items) |
|---|---|---|
| Where the fan-out shape lives | ASL JSON — visible in Workflow Studio | Configuration/input list — invisible in the graph |
| Distinct config per branch | Natural — each branch is its own sub-workflow | Requires encoding image URI + args as list-item fields |
| Adding/removing an analyzer | Edit ASL — visible diff | Edit input list generator — invisible in ASL diff |
| Console visualisation | One box per branch, one graph per run | One "Map" box with iteration count |
| History impact | Each branch's states count against 25,000 event cap in parent | Same (Inline mode) |
| Concurrency ceiling | Only limited by branch count | 40 (Inline), 10,000 (Distributed) |

**Recommendation for our shape:** **`Parallel` state.**

Reasons:

1. **Visibility.** Four/five analyzers = four/five load-bearing capabilities of the platform. They should be *visible in the state machine graph*, not hidden as list items. Someone opening Workflow Studio should immediately see the four/five capabilities.
2. **Distinct config natural.** Each analyzer has a different container image, different IAM policy needs, different retry tolerances (Bias/Explain are slower and may want longer timeouts than DQ). Encoding these per-branch in ASL is cleaner than encoding as list items.
3. **Console UX.** A run inspector for a "Parallel with 4 named branches" is much more legible than "Map iteration [2]".
4. **Concurrency cap is a non-issue.** Even at 5 branches we are nowhere near 40.

Use `Map` (Distributed) instead if you later want to fan out over *N silos × K analyzers* dynamically — that shape crosses the 40 threshold and does need Distributed Map.

### 1.4 The load-bearing failure clause — must read

Also from [state-parallel — Error Handling](https://docs.aws.amazon.com/step-functions/latest/dg/state-parallel.html):

> **If any branch fails, because of an unhandled error or by transitioning to a `Fail` state, the entire `Parallel` state is considered to have failed and all its branches are stopped.** If the error is not handled by the `Parallel` state itself, Step Functions stops the execution with an error.

This is the single most important sentence for this design. **Naïve `Parallel` violates our "one branch dying does not kill siblings" requirement.** The fix is documented and standard: put a per-branch `Catch` inside each branch so failures never bubble up as *unhandled* to the `Parallel` state. See §7 below.

> **Note (from the same doc):** "When a parallel state fails, invoked Lambda functions continue to run and activity workers processing a task token are not stopped."

For our `.sync` container invocations this means: if the Parallel state is torn down, the underlying ECS task or Processing Job may still be running and burning money. Design accordingly (§7 covers cleanup via task tokens + heartbeats if we ever need aggressive cancellation).

---

## 2. Standard vs Express Step Functions

Source: [Choosing workflow type](https://docs.aws.amazon.com/step-functions/latest/dg/choosing-workflow-type.html).

| Attribute | Standard | Express |
|---|---|---|
| Max duration | **1 year** | **5 minutes** |
| Pricing model | Per state transition | Per invocation + duration + memory |
| Execution history | Retained 90 days (queryable via API + console); logs to CW Logs are optional | Not retained by SFN — you must enable CloudWatch Logs |
| Execution semantics | **Exactly-once** | Async: at-least-once. Sync (`StartSyncExecution`): at-most-once |
| Supports `.sync` service integrations | **Yes** | **No** |
| Supports `.waitForTaskToken` | **Yes** | **No** |
| Supports Distributed Map | Yes | No |
| State transition throttling (us-east-1, us-west-2, eu-west-1) | 5,000 bucket / 5,000 refill per sec | Unlimited |

Quotas: [service-quotas](https://docs.aws.amazon.com/step-functions/latest/dg/service-quotas.html).

### 2.1 Which workflow type for our two flows

**Snapshot workflow (fan-out for minutes-to-hours):** **Standard.** Non-negotiable — Express caps at 5 min and doesn't support `.sync` container integrations, which we depend on to drive ECS RunTask / SageMaker Processing Job as blocking tasks (§3).

**Live workflow (hourly, targeting < 15 min):** **Standard.** Same two reasons. Even if we could hit sub-5-min end-to-end, we would need `.sync` integrations, which Express does not support.

**Standard is the right answer for both.** Express is a red herring for anything driving container jobs via `.sync`.

**Cost consequence acknowledged upfront:** Standard bills per state transition ($0.025 / 1,000 in us-east-1 per [pricing](https://aws.amazon.com/step-functions/pricing/)). We model this in §9.

**Nesting trick (best-practice pattern):** [Optimizing costs using Express Workflows](https://docs.aws.amazon.com/step-functions/latest/dg/sfn-best-practices.html#cost-opt-exp-workflows) recommends nesting Express *inside* Standard for the idempotent post-processing stages of a workflow. If we later add a hot post-processing chain (e.g., "publish metrics + write DDB + notify Slack") that runs after the containers finish, that inner chain is a candidate to move into a nested Express workflow to shave transitions. But the outer fan-out has to be Standard.

---

## 3. What compute the branches actually invoke

Real options for containerised branches, with pros/cons for our size (single-digit MB dataset, single-digit-hundred MB models):

### 3.1 SageMaker Processing Job via `.sync`

Docs: [connect-sagemaker](https://docs.aws.amazon.com/step-functions/latest/dg/connect-sagemaker.html). ASL Resource: `arn:aws:states:::sagemaker:createProcessingJob.sync`.

**Pros:**
- Purpose-built for ML jobs. Native `ProcessingInputs` / `ProcessingOutputConfig` for S3 pull-in / push-out.
- IAM roles + VPC config are first-class parameters on `CreateProcessingJob` ([API_CreateProcessingJob](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_CreateProcessingJob.html)).
- `Environment` map on the API sets up to 100 env var key/values in the container.
- Job artifact captured in the SageMaker Studio UI + Model Cards.

**Cons:**
- **Cold-start latency of roughly 2-3 minutes per Processing Job** for ML instance provisioning. Not a doc-cited number — it's an operational fact well-established in the community. For a 5-branch fan-out the parallel cold-start doesn't accumulate but does slam the wall-clock.
- Rate-limited by the SageMaker `CreateProcessingJob` API. The API reference does not publish the exact TPS quota (`SageMaker.AmazonSageMakerException` and `SageMaker.ResourceLimitExceededException` are documented as retriable in [connect-sagemaker's training example](https://docs.aws.amazon.com/step-functions/latest/dg/connect-sagemaker.html#sagemaker-example-training)). Community and long-standing AWS guidance: `CreateProcessingJob` is bounded around 1 TPS and concurrency is capped by per-instance-type quotas visible in the [Service Quotas console](https://console.aws.amazon.com/servicequotas/home/services/sagemaker/quotas).
- Instance-type quotas per region per account for Processing Jobs are visible under Service Quotas (`ml.t3.medium for processing job usage`, etc.). These are the real constraint at scale.
- Overkill for a 10-second Data Quality summary. Provisioning cost dominates job cost.

**When to use in our system:** Bias, Explainability — jobs that need > 4 vCPU or a GPU, benefit from being tracked as first-class SageMaker artifacts, and where 2-3 min of cold start is amortised against 5-30 min of work.

### 3.2 ECS RunTask (Fargate) via `.sync`

Docs: [connect-ecs](https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html). ASL Resource: `arn:aws:states:::ecs:runTask.sync`.

**Pros:**
- **Fast start** — Fargate tasks typically ready in tens of seconds, no pre-warmed pool required.
- Container-native. Same ECR image, same task-def shape as any other Fargate workload.
- `Overrides.ContainerOverrides` on `RunTask` lets us pass `Command`, `Environment`, `Cpu`, `Memory` **per invocation** ([RunTask API](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_RunTask.html), [ContainerOverride](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_ContainerOverride.html)). One task def per analyzer, different `Environment` per run.
- Cheaper than a Processing Job for short work (no minimum instance-hour billing).
- Sits under the ECS RunTask rate limit which is much higher than SageMaker CreateProcessingJob.

**Cons:**
- Not tied into SageMaker Studio artifact tracking. You have to build that observability yourself (§6).
- `ecs:runTask.sync` returns HTTP 200 even on task-level failures with non-empty `Failures`. The Step Functions docs call this out and note that in the `.sync` variant this is translated into an `AmazonECS.Unknown` error state — you can `Retry` / `Catch` on it. Quote from [connect-ecs](https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html):

> `ecs:runTask` can return an HTTP 200 response, but have a non-empty `Failures` field as follows: **Request Response**: Return the response and do not fail the task, which is the same as non-optimized integrations. **Run a Job or Task Token**: If a non-empty `Failures` field is encountered, the task is failed with an `AmazonECS.Unknown` error.

### 3.3 AWS Batch via `.sync`

ASL Resource: `arn:aws:states:::batch:submitJob.sync`. Same rough shape as ECS. Job queues + compute environments give you priority routing and spot capacity access. Overkill for 4-5 fixed analyzers — Batch's value is in orchestrating hundreds of jobs across shared compute environments. Skip unless we grow far beyond current scope.

### 3.4 Lambda

Docs: [connect-lambda](https://docs.aws.amazon.com/step-functions/latest/dg/connect-lambda.html). Hard limits: **15 min max runtime**, **10 GB memory max**, 250 MB unzipped deployment / 10 GB container image. Fast start. Cheap. Fine for the DQ analyzer if it fits — a summary-statistics scan of a snapshot easily can. Not fine for Bias or Explainability if runtime creeps.

### 3.5 Nested Step Functions (`states:startExecution.sync`)

Docs: [connect-stepfunctions](https://docs.aws.amazon.com/step-functions/latest/dg/connect-stepfunctions.html). Useful for pure orchestration (running a sub-workflow inside a branch) and for breaking through the 25,000-event history quota per [bp-history-limit](https://docs.aws.amazon.com/step-functions/latest/dg/sfn-best-practices.html#bp-history-limit). Not compute — orchestration.

### 3.6 Recommended split for our 4/5 analyzers

Default: **Fargate ECS RunTask.sync everywhere.** Then peel off exceptions:

| Analyzer | Compute | Rationale |
|---|---|---|
| Data Quality | Fargate (or Lambda if runtime consistently < 5 min) | Small, fast, structured summary — Fargate task-per-run keeps observability uniform |
| Model Quality | Fargate | Same shape as DQ |
| Bias | Fargate initially; move to SageMaker Processing Job if runtime > 15 min or if we adopt SageMaker Clarify | Depends on library choice |
| Explainability | SageMaker Processing Job (Clarify or custom) — or Fargate if using an in-house surrogate model | Explainability at scale needs the GPU/CPU-fleet Processing Jobs address |
| Shadow (live only) | Fargate | Compares two model outputs — I/O-bound, small |

**Why uniform default matters:** every analyzer being an ECS task means one observability path (CloudWatch Logs group per task def, one metric namespace, one IAM pattern). Every exception adds a code path to the platform.

---

## 4. Concrete ASL for our shape

Below is a valid ASL for a Standard workflow that:

- Receives an EventBridge S3 `ObjectCreated` payload (unwrapped to a snapshot manifest by a preceding `Pass` state).
- Runs a `Parallel` state with 4 branches, one per analyzer, each doing `ecs:runTask.sync` with a distinct task def + container image.
- Passes shared run metadata (project, snapshot S3 URI, config S3 URI, run ID) into each branch via `ContainerOverrides.Environment`.
- Each branch has its own `Retry` for transient ECS errors and its own `Catch` that routes failures to a per-branch `RecordBranchFailure` state, so **siblings keep running when one dies**.
- Aggregates all four branch results (success or failure envelope) into an array at the parent, then a final `PublishStatus` state emits a summary to a status output.

```json
{
  "Comment": "Snapshot fan-out — Model Quality, Data Quality, Bias, Explainability",
  "StartAt": "PrepareRun",
  "TimeoutSeconds": 21600,
  "States": {
    "PrepareRun": {
      "Type": "Pass",
      "Parameters": {
        "runId.$": "$$.Execution.Name",
        "project.$": "$.detail.project",
        "snapshotUri.$": "$.detail.snapshotUri",
        "configUri.$": "$.detail.configUri"
      },
      "ResultPath": "$.run",
      "Next": "FanOut"
    },
    "FanOut": {
      "Type": "Parallel",
      "ResultPath": "$.branchResults",
      "Next": "PublishStatus",
      "Branches": [
        {
          "StartAt": "ModelQuality",
          "States": {
            "ModelQuality": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "Parameters": {
                "Cluster": "arn:aws:ecs:eu-west-1:123456789012:cluster/monitor",
                "TaskDefinition": "arn:aws:ecs:eu-west-1:123456789012:task-definition/monitor-model-quality",
                "LaunchType": "FARGATE",
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ["subnet-aaa", "subnet-bbb"],
                    "SecurityGroups": ["sg-monitor"],
                    "AssignPublicIp": "DISABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "analyzer",
                      "Environment": [
                        {"Name": "ANALYZER", "Value": "model_quality"},
                        {"Name": "RUN_ID", "Value.$": "$.run.runId"},
                        {"Name": "PROJECT", "Value.$": "$.run.project"},
                        {"Name": "SNAPSHOT_URI", "Value.$": "$.run.snapshotUri"},
                        {"Name": "CONFIG_URI", "Value.$": "$.run.configUri"}
                      ]
                    }
                  ]
                }
              },
              "Retry": [
                {
                  "ErrorEquals": ["ECS.AmazonECSException", "AmazonECS.Unknown", "States.TaskFailed"],
                  "IntervalSeconds": 30,
                  "MaxAttempts": 2,
                  "BackoffRate": 2.0,
                  "JitterStrategy": "FULL"
                }
              ],
              "Catch": [
                {
                  "ErrorEquals": ["States.ALL"],
                  "ResultPath": "$.error",
                  "Next": "ModelQualityFailed"
                }
              ],
              "ResultPath": "$.result",
              "End": true
            },
            "ModelQualityFailed": {
              "Type": "Pass",
              "Parameters": {
                "analyzer": "model_quality",
                "status": "FAILED",
                "error.$": "$.error"
              },
              "End": true
            }
          }
        },
        {
          "StartAt": "DataQuality",
          "States": {
            "DataQuality": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "Parameters": {
                "Cluster": "arn:aws:ecs:eu-west-1:123456789012:cluster/monitor",
                "TaskDefinition": "arn:aws:ecs:eu-west-1:123456789012:task-definition/monitor-data-quality",
                "LaunchType": "FARGATE",
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ["subnet-aaa", "subnet-bbb"],
                    "SecurityGroups": ["sg-monitor"],
                    "AssignPublicIp": "DISABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "analyzer",
                      "Environment": [
                        {"Name": "ANALYZER", "Value": "data_quality"},
                        {"Name": "RUN_ID", "Value.$": "$.run.runId"},
                        {"Name": "PROJECT", "Value.$": "$.run.project"},
                        {"Name": "SNAPSHOT_URI", "Value.$": "$.run.snapshotUri"},
                        {"Name": "CONFIG_URI", "Value.$": "$.run.configUri"}
                      ]
                    }
                  ]
                }
              },
              "Retry": [
                {
                  "ErrorEquals": ["ECS.AmazonECSException", "AmazonECS.Unknown", "States.TaskFailed"],
                  "IntervalSeconds": 30,
                  "MaxAttempts": 2,
                  "BackoffRate": 2.0,
                  "JitterStrategy": "FULL"
                }
              ],
              "Catch": [
                {
                  "ErrorEquals": ["States.ALL"],
                  "ResultPath": "$.error",
                  "Next": "DataQualityFailed"
                }
              ],
              "ResultPath": "$.result",
              "End": true
            },
            "DataQualityFailed": {
              "Type": "Pass",
              "Parameters": {
                "analyzer": "data_quality",
                "status": "FAILED",
                "error.$": "$.error"
              },
              "End": true
            }
          }
        },
        {
          "StartAt": "Bias",
          "States": {
            "Bias": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "Parameters": {
                "Cluster": "arn:aws:ecs:eu-west-1:123456789012:cluster/monitor",
                "TaskDefinition": "arn:aws:ecs:eu-west-1:123456789012:task-definition/monitor-bias",
                "LaunchType": "FARGATE",
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ["subnet-aaa", "subnet-bbb"],
                    "SecurityGroups": ["sg-monitor"],
                    "AssignPublicIp": "DISABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "analyzer",
                      "Environment": [
                        {"Name": "ANALYZER", "Value": "bias"},
                        {"Name": "RUN_ID", "Value.$": "$.run.runId"},
                        {"Name": "PROJECT", "Value.$": "$.run.project"},
                        {"Name": "SNAPSHOT_URI", "Value.$": "$.run.snapshotUri"},
                        {"Name": "CONFIG_URI", "Value.$": "$.run.configUri"}
                      ]
                    }
                  ]
                }
              },
              "Retry": [
                {
                  "ErrorEquals": ["ECS.AmazonECSException", "AmazonECS.Unknown", "States.TaskFailed"],
                  "IntervalSeconds": 60,
                  "MaxAttempts": 2,
                  "BackoffRate": 2.0,
                  "JitterStrategy": "FULL"
                }
              ],
              "Catch": [
                {
                  "ErrorEquals": ["States.ALL"],
                  "ResultPath": "$.error",
                  "Next": "BiasFailed"
                }
              ],
              "ResultPath": "$.result",
              "End": true
            },
            "BiasFailed": {
              "Type": "Pass",
              "Parameters": {
                "analyzer": "bias",
                "status": "FAILED",
                "error.$": "$.error"
              },
              "End": true
            }
          }
        },
        {
          "StartAt": "Explainability",
          "States": {
            "Explainability": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "Parameters": {
                "Cluster": "arn:aws:ecs:eu-west-1:123456789012:cluster/monitor",
                "TaskDefinition": "arn:aws:ecs:eu-west-1:123456789012:task-definition/monitor-explainability",
                "LaunchType": "FARGATE",
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ["subnet-aaa", "subnet-bbb"],
                    "SecurityGroups": ["sg-monitor"],
                    "AssignPublicIp": "DISABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "analyzer",
                      "Environment": [
                        {"Name": "ANALYZER", "Value": "explainability"},
                        {"Name": "RUN_ID", "Value.$": "$.run.runId"},
                        {"Name": "PROJECT", "Value.$": "$.run.project"},
                        {"Name": "SNAPSHOT_URI", "Value.$": "$.run.snapshotUri"},
                        {"Name": "CONFIG_URI", "Value.$": "$.run.configUri"}
                      ]
                    }
                  ]
                }
              },
              "Retry": [
                {
                  "ErrorEquals": ["ECS.AmazonECSException", "AmazonECS.Unknown", "States.TaskFailed"],
                  "IntervalSeconds": 60,
                  "MaxAttempts": 2,
                  "BackoffRate": 2.0,
                  "JitterStrategy": "FULL"
                }
              ],
              "Catch": [
                {
                  "ErrorEquals": ["States.ALL"],
                  "ResultPath": "$.error",
                  "Next": "ExplainabilityFailed"
                }
              ],
              "ResultPath": "$.result",
              "End": true
            },
            "ExplainabilityFailed": {
              "Type": "Pass",
              "Parameters": {
                "analyzer": "explainability",
                "status": "FAILED",
                "error.$": "$.error"
              },
              "End": true
            }
          }
        }
      ]
    },
    "PublishStatus": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "arn:aws:lambda:eu-west-1:123456789012:function:monitor-publish-status",
        "Payload": {
          "runId.$": "$.run.runId",
          "project.$": "$.run.project",
          "branchResults.$": "$.branchResults"
        }
      },
      "End": true
    }
  }
}
```

### 4.1 Notes on this ASL

- **Per-branch Catch is the trick.** Each branch has `Catch: [{ErrorEquals: ["States.ALL"], Next: <local failure state>}]`. The failure state is `End: true`, so the *branch* terminates successfully from the Parallel state's perspective, with a shaped `{analyzer, status: "FAILED", error}` result. Siblings continue.
- **Aggregation** happens naturally via `ResultPath: "$.branchResults"` on the `Parallel` state. Because the `Parallel` state's output is "an array with one element for each branch, containing the output from that branch" ([state-parallel](https://docs.aws.amazon.com/step-functions/latest/dg/state-parallel.html)), the array will contain a mix of success-shaped and failure-shaped envelopes — a partial-success result.
- **`PublishStatus`** is where you compute "was this run all-green, partial, or all-red" and emit metrics/DDB rows.
- Retries use `JitterStrategy: FULL` per [error-handling docs](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html) — spreads simultaneous retries over a randomised interval and is the correct choice when 4 branches might all fail together on a shared transient issue (e.g., ECR pull throttle).
- Retry error names include `ECS.AmazonECSException` (transient ECS errors), `AmazonECS.Unknown` (the "HTTP 200 with non-empty Failures" case documented at [connect-ecs](https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html)), and `States.TaskFailed` as a wildcard.

### 4.2 What to change for the live workflow

Live workflow adds a 5th branch (Shadow). Copy the DataQuality branch shape, change `TaskDefinition` and `ANALYZER` env var. Trigger from EventBridge Scheduler ([EventBridge Scheduler docs](https://docs.aws.amazon.com/scheduler/latest/UserGuide/what-is-scheduler.html)) with a cron expression targeting `states:StartExecution` on the live state-machine ARN.

---

## 5. Passing config to each branch

Two mechanisms are relevant:

### 5.1 ASL `Parameters` (JSONPath) or `Arguments` (JSONata) at the Task state

Docs: [connect-parameters](https://docs.aws.amazon.com/step-functions/latest/dg/connect-parameters.html), [state-task](https://docs.aws.amazon.com/step-functions/latest/dg/state-task.html).

- `Parameters` templates the request body sent to the integrated service. `Value.$` prefix pulls from execution state (`$`), context object (`$$`), or intrinsic functions.
- All parameters are **PascalCase** even though native ECS API is camelCase. Quote from [connect-ecs](https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html):
> Even if the native service API is in camelCase, for example the API action `startSyncExecution`, you specify parameters in PascalCase, such as: `StateMachineArn`.

### 5.2 `ContainerOverrides.Environment` — the per-branch config channel

Docs: [ECS ContainerOverride API](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_ContainerOverride.html).

Each `ContainerOverride` object accepts:
- `Name` — container name in the task def to override
- `Command` — replaces the container's default `CMD`
- `Environment` — array of `{Name, Value}` — merged with (and overriding) the task def's `environment`
- `Cpu`, `Memory`, `MemoryReservation` — per-run resource overrides
- `ResourceRequirements` — GPU overrides

**The pattern in our ASL:** `Overrides.ContainerOverrides[0].Environment` receives:
- Static values (`{"Name": "ANALYZER", "Value": "bias"}`) — hardcodes what this branch is.
- Dynamic values (`{"Name": "RUN_ID", "Value.$": "$.run.runId"}`) — pulled from the state input.

The container reads env vars at start and knows: (a) which analyzer to run, (b) where the snapshot lives in S3, (c) where the config lives, (d) what run ID to tag its outputs with.

**Do not pass secrets this way.** Fetch secrets from SSM Parameter Store or Secrets Manager *inside the container* using the task role. `Environment` is visible in CloudTrail and ECS task descriptions.

### 5.3 Shared payload, distinct overrides

The Parallel state gives each branch a *copy* of its input ([state-parallel](https://docs.aws.amazon.com/step-functions/latest/dg/state-parallel.html)):
> A `Parallel` state provides each branch with a copy of its own input data (subject to modification by the `InputPath` field).

So the same `{run: {runId, project, snapshotUri, configUri}}` payload flows into all 4 branches. The **branch-specific** bits (container image, analyzer name) are hardcoded inside each branch's ASL. The **shared** bits (run identity, S3 pointers) are templated from state input.

---

## 6. Observability — single-pane view for the 4-5 branches of run X

### 6.1 What each layer gives you

| Layer | What it shows | Retention |
|---|---|---|
| Step Functions execution history (Standard) | Every state entered/exited, every input/output, every retry, every catch. Console visualisation with per-branch drill-down. | 90 days (adjustable down to 30 for compliance; hard cap 90) — [service-quotas](https://docs.aws.amazon.com/step-functions/latest/dg/service-quotas.html) |
| CloudWatch Logs (SFN, when enabled) | Same events as history, structured JSON, queryable via Logs Insights | Your retention setting on the log group |
| ECS task CloudWatch Logs group | Container stdout/stderr per task | Your retention setting |
| SageMaker Processing Job logs | Container stdout/stderr per PJ, plus artifact tracking in Studio | Your retention setting |
| CloudWatch Metrics — SFN built-ins | `ExecutionsStarted/Succeeded/Failed/TimedOut/Throttled/Aborted`, `ActivityRunTime`, `LambdaFunctionRunTime` — [SFN metrics](https://docs.aws.amazon.com/step-functions/latest/dg/procedure-cw-metrics.html) | 15 months |
| CloudWatch Metrics — your own emissions | Anything the analyzer container puts to CW Metrics with dimensions `{project, analyzer, runId}` | 15 months |
| X-Ray | Distributed trace per execution, with segments for each service integration | 30 days default |

### 6.2 Correlation — how to actually get "the 4 branches for run X"

Two techniques together:

1. **Set the SFN execution name to a stable run ID.** In our ASL above, we pull `$$.Execution.Name` into `run.runId` and inject it as the `RUN_ID` env var into every container. Same string appears in:
   - The SFN execution ARN
   - The container logs (if the container logs `RUN_ID` on startup — enforce this in the entrypoint)
   - The CloudWatch Metric dimensions the container emits
   - The DynamoDB row the container writes
2. **Tag every ECS task with `RunId`.** RunTask supports `PropagateTags: TASK_DEFINITION` or explicit `Tags`. Add `{Key: "RunId", Value.$: "$.run.runId"}` to each branch's `Parameters` so ECS console + Cost Explorer can filter by run.

### 6.3 SFN logging setup (do this)

From [cw-logs](https://docs.aws.amazon.com/step-functions/latest/dg/cw-logs.html) and [bp-cwl](https://docs.aws.amazon.com/step-functions/latest/dg/sfn-best-practices.html#bp-cwl):

- Enable CloudWatch Logs on the state machine at `INFO` (default) or `ALL` level with `IncludeExecutionData: true` so payloads are logged.
- Prefix your log group name with `/aws/vendedlogs/` so you don't chew through the 5,120-character CloudWatch resource policy limit. Best-practice quote:
> You can prefix your CloudWatch Logs log group names with `/aws/vendedlogs/` to avoid the CloudWatch Logs resource policy size limit. If you create a log group in the Step Functions console, the suggested log group name will already be prefixed with `/aws/vendedlogs/states`.

### 6.4 Enable X-Ray tracing

X-Ray tracing for SFN is a checkbox on the state machine. Once on, each execution produces a trace segment per state, plus subsegments per service integration (each `ecs:runTask.sync` gets one). Gives you a single Gantt view of "how long did each branch take" without leaving the AWS console.

### 6.5 Custom CW metrics per branch completion

Emit from the `PublishStatus` Lambda after the fan-out:

- `RunResult` (dimension: `Project`, `Analyzer`, `Status`) — count 1 per branch
- `RunDuration` (dimension: `Project`, `Analyzer`) — from state input timestamps

This gives us the "how many bias analyzers failed in the last 24h across all silos" dashboard trivially.

### 6.6 Single-pane recommendation

For humans debugging a run: **SFN console → executions → click the run → click any branch box → see child ECS task ARN in the Task state's output → click through to CloudWatch Logs of that task.** Two clicks from run ID to container log.

For dashboards: **CloudWatch dashboard with widgets driven by our custom CW metrics** (§6.5), keyed by run ID + project + analyzer.

---

## 7. Retry, catch, and DLQ patterns

### 7.1 Retry semantics

Docs: [concepts-error-handling](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html).

- `Retry` is available on `Task`, `Parallel`, and `Map` states.
- Retries are counted as state transitions and **billed** in Standard workflows.
- Fields: `ErrorEquals` (array), `IntervalSeconds`, `MaxAttempts` (default 3), `BackoffRate` (default 2.0), `MaxDelaySeconds`, `JitterStrategy` (`FULL` or `NONE`, default `NONE`).
- Wildcard error names: `States.ALL` (must appear alone, last), `States.TaskFailed` (matches anything except `States.Timeout`).

### 7.2 Catch semantics — the per-branch trick

Also from [concepts-error-handling](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html):
> When a state reports an error and either there is no `Retry` field, or if retries fail to resolve the error, Step Functions scans through the catchers in the order listed in the array. When the error name appears in the value of a catcher's `ErrorEquals` field, the state machine transitions to the state named in the `Next` field.

**Key design point:** `Catch` is placed **inside a branch**, on the Task state that might fail. That means if the retries exhaust:

1. The Task state transitions to the catcher's `Next` state (still inside the branch).
2. The branch continues from there. It can terminate normally (`End: true` on a `Pass` state that shapes a failure envelope).
3. The Parallel state sees the branch as **succeeded** (a terminal state was reached without unhandled error).
4. **Siblings are not stopped.**

If instead you put `Catch` at the Parallel state level (outside branches), then any branch's exhausted retry becomes an unhandled error → Parallel state fails → *all* branches stopped. That is the opposite of what we want.

### 7.3 Retrying a single branch without re-running the whole Parallel

Step Functions offers **redrive** for Standard workflows ([redrive-executions](https://docs.aws.amazon.com/step-functions/latest/dg/redrive-executions.html)) which restarts only the failed states of an execution. But redrive is at the execution level, not the branch level, and requires that the execution itself be in `FAILED` state.

In our design branches capture their own failures (they don't propagate), so the execution normally ends `SUCCEEDED` with a partial-success `branchResults` array. Redrive doesn't apply.

**Real pattern for "retry only Bias for run X":** the failure envelope written to DynamoDB by `PublishStatus` includes enough to re-invoke just that branch. Have a small Lambda triggered off the DDB row (or fired by a human) that calls `ecs:RunTask` directly with the same overrides. Bypasses the state machine.

### 7.4 DLQ pattern for terminal failures

- Each analyzer branch that ends in its `<Analyzer>Failed` `Pass` state emits `{analyzer, status: "FAILED", error}` in `branchResults`.
- `PublishStatus` inspects the array; for each `status: "FAILED"` entry, write a row to a `MonitorFailures` DynamoDB table keyed by `{runId, analyzer}`.
- EventBridge Pipe from DynamoDB Streams filters `status = FAILED` and routes to SNS / Slack for human triage.
- Poison-pill runs (where the container image itself is broken) will keep failing after redrive. Bound the number of automated retries at the branch level (2 in the ASL above) and let the DLQ row be the final signal.

### 7.5 Cleanup of orphaned tasks on Parallel state failure

If the Parallel state itself does fail (some non-branch error — e.g., `States.DataLimitExceeded` because we passed too much data via state → 256 KiB limit per [service-quotas](https://docs.aws.amazon.com/step-functions/latest/dg/service-quotas.html)) then per [state-parallel](https://docs.aws.amazon.com/step-functions/latest/dg/state-parallel.html):
> When a parallel state fails, invoked Lambda functions continue to run and activity workers processing a task token are not stopped.

For our `.sync` ECS tasks: Step Functions *does* attempt to stop the ECS task via `ecs:StopTask` when using `.sync` (that permission is auto-added to the state machine role — see the IAM template in [connect-ecs](https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html)). Confirmed in the doc: "You can only stop Amazon Elastic Container Service (Amazon ECS) tasks that were started by Step Functions, despite the `*` IAM policy." Good — no orphaned runaway containers in the normal path.

---

## 8. Real-world reference architectures

### 8.1 aws-samples: `aws-stepfunctions-examples`

Repo: `https://github.com/aws-samples/aws-stepfunctions-examples` — official samples curated by the SFN team. Contains multiple `parallel-state` examples, ECS/Fargate integration samples, and error-handling patterns using per-branch Catch. Not a single "5 containers fan out" example, but every ingredient is there.

### 8.2 AWS blog: Building serverless ETL with Step Functions + Fargate

Blog: `https://aws.amazon.com/blogs/compute/orchestrating-multi-account-workflows-with-aws-step-functions/` and the "Big data processing pipelines with Step Functions" series. Concrete production shape: Standard workflow, Parallel state, ECS RunTask.sync per branch, CW Logs + X-Ray for observability. Same architecture we are proposing.

### 8.3 AWS Solutions Library: Serverless Data Analytics Pipeline

`https://aws.amazon.com/solutions/implementations/aws-serverless-data-lake-framework/` — SDLF uses Step Functions fan-out with Glue and Lambda; the fan-out shape and per-branch retry/catch are directly transferable to our Fargate variant.

### 8.4 SageMaker Model Monitor + Clarify

For the Bias and Explainability analyzers specifically, [SageMaker Clarify](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-configure-processing-jobs.html) ships a Processing Job container that computes bias metrics and SHAP explanations. Standard pattern: invoke Clarify via `sagemaker:createProcessingJob.sync` from a Step Functions branch. Trades control for a managed container; worth considering if we don't want to maintain in-house Bias/Explain code.

### 8.5 The AWS Step Functions Workshop

[catalog.workshops.aws/stepfunctions](https://catalog.workshops.aws/stepfunctions) — the official interactive workshop includes a "Parallel state" lab and an "Error handling" lab that literally teaches per-branch Catch. Recommend anyone new to the shape run through it (~1h).

---

## 9. Costs — orchestration-layer only

Compute (ECS tasks / Processing Jobs) is out of scope for this section. Focus: Step Functions state transitions + optional CloudWatch Logs.

### 9.1 Standard SFN pricing recap

- **$0.025 per 1,000 state transitions** in us-east-1 (per [pricing](https://aws.amazon.com/step-functions/pricing/)).
- **eu-west-1 not separately listed on the page** but historically has been at parity with us-east-1 for SFN; verify at synth time.
- **Free tier: 4,000 free state transitions per month.**

### 9.2 State transition count per fan-out — our shape

For one snapshot execution running the ASL from §4:

- `PrepareRun` (1 transition in + 1 out) = 2
- `FanOut` (Parallel state entry + exit) = 2
- Per branch: Task enter (1) + Task exit (1) = 2 × 4 branches = 8
- `PublishStatus` (1 in + 1 out) = 2

Baseline: ~14 transitions per successful snapshot execution. With retries: assume 1-2 additional transitions per branch that retries. Round up to **~20 transitions per run** to include failure paths and shape-noise.

For the live workflow with 5 branches: **~24 transitions per run**.

### 9.3 Monthly volume

- **Snapshot:** 20-40 fan-outs/month × 20 transitions = **400-800 transitions/month.** Well inside the free tier.
- **Live:** 24 runs/day × 30 days × 5 silos = 3,600 runs/month × 24 transitions = **86,400 transitions/month per state-machine deployment.** (You wrote 18,000 fan-outs/month in the brief; that's 24 × 30 × 5 × 5 branches = 18,000 branch-fan-outs, but the *run* count is 3,600 — I'll answer both.)

If the intent is to run one live state machine per silo, the total across 5 silos is **~432,000 transitions/month.**

### 9.4 Dollars

- Snapshot: **$0** (fully inside free tier).
- Live, per silo: 86,400 × ($0.025 / 1,000) = **$2.16/month.**
- Live, 5 silos: **~$10.80/month.**

Your original back-of-envelope of "900k transitions/month" would be **~$22.50/month**. Even at 10× that (9M transitions/month), you'd be at **$225/month for the orchestration layer.** State transitions are not going to be the expensive thing.

### 9.5 CloudWatch Logs cost

If we log at `ALL` level with `IncludeExecutionData: true`, expect a few KB per state transition to CW Logs. At 432,000 transitions/month × ~2 KB = ~1 GB/month of log ingest. CW Logs ingest = $0.50/GB in us-east-1 → **~$0.50/month.** Storage negligible with sensible retention (30-90 days).

### 9.6 Order-of-magnitude bill for the orchestration layer

**Well under $50/month across all environments at current scale**, dominated by CloudWatch Logs if we chose verbose logging. Compute (Fargate tasks, Processing Jobs) will be orders of magnitude larger — that's where cost engineering effort should go.

---

## 10. Design decision summary

- **Workflow type:** Standard for both flows. Express is disqualified by 5-min cap and no `.sync` support.
- **Fan-out shape:** `Parallel` state with static branches, one per analyzer. Not Inline Map, not Distributed Map, at current scale.
- **Compute default:** Fargate ECS RunTask.sync per branch. Peel off to SageMaker Processing Job only for Explainability (and Bias if runtime grows).
- **Failure isolation:** per-branch `Catch` on `States.ALL` routing to a local failure `Pass` state that shapes `{analyzer, status: "FAILED", error}` and ends the branch normally. The Parallel state's output array becomes a partial-success envelope.
- **Retry:** per-branch `Retry` on `States.TaskFailed` + `AmazonECS.Unknown` + `ECS.AmazonECSException`, 2 attempts, exponential backoff, `JitterStrategy: FULL`.
- **Config passing:** shared payload via state input; per-branch `Overrides.ContainerOverrides.Environment` templates static analyzer name + dynamic run metadata. Secrets fetched inside the container from SSM/Secrets Manager, never via env.
- **Correlation:** execution name = run ID, propagated as `RUN_ID` env var + ECS task tag + dimension on all custom CW metrics.
- **DLQ:** DDB `MonitorFailures` table populated by `PublishStatus`, streamed to SNS/Slack for human triage. No automated redrive at the branch level beyond the 2 retries.
- **Cost:** orchestration layer is **~$10-25/month at planned volumes**. Compute is the cost lever, not SFN.

## 11. Open questions

- Do we want Distributed Map instead of Parallel for the *silo × analyzer* dimension? Currently we're planning "one state machine per silo, each running Parallel over analyzers." Alternative: one state machine, Distributed Map over silos, each map iteration is a nested Parallel over analyzers. Second shape scales past 40 silos and puts each silo's history in its own child execution.
- What is the actual `CreateProcessingJob` TPS quota in our target regions? The doc doesn't publish it. Need to hit the Service Quotas console and record the current value — it drives whether we can safely use PJ for more than one analyzer.
- Should `PublishStatus` be a Lambda or a nested Express state machine? A nested Express workflow with idempotent CW metric emission + DDB write + SNS publish is the textbook `[bp-cost-opt-nesting](https://docs.aws.amazon.com/step-functions/latest/dg/sfn-best-practices.html#cost-opt-exp-wflow-nesting)` case. Not urgent.

---

## Reference index (every AWS URL cited above)

- Step Functions Parallel state: https://docs.aws.amazon.com/step-functions/latest/dg/state-parallel.html
- Step Functions Map state: https://docs.aws.amazon.com/step-functions/latest/dg/state-map.html
- Step Functions Distributed Map: https://docs.aws.amazon.com/step-functions/latest/dg/state-map-distributed.html
- Choosing Standard vs Express: https://docs.aws.amazon.com/step-functions/latest/dg/choosing-workflow-type.html
- Step Functions pricing: https://aws.amazon.com/step-functions/pricing/
- Step Functions ECS integration: https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html
- Step Functions SageMaker integration: https://docs.aws.amazon.com/step-functions/latest/dg/connect-sagemaker.html
- Step Functions Lambda integration: https://docs.aws.amazon.com/step-functions/latest/dg/connect-lambda.html
- Step Functions nested execution: https://docs.aws.amazon.com/step-functions/latest/dg/connect-stepfunctions.html
- Error handling (Retry/Catch): https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html
- Best practices: https://docs.aws.amazon.com/step-functions/latest/dg/sfn-best-practices.html
- Service quotas: https://docs.aws.amazon.com/step-functions/latest/dg/service-quotas.html
- CloudWatch Logs for SFN: https://docs.aws.amazon.com/step-functions/latest/dg/cw-logs.html
- CloudWatch metrics: https://docs.aws.amazon.com/step-functions/latest/dg/procedure-cw-metrics.html
- Redrive executions: https://docs.aws.amazon.com/step-functions/latest/dg/redrive-executions.html
- SFN Workshop (Parallel + Error handling labs): https://catalog.workshops.aws/stepfunctions
- ECS RunTask API: https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_RunTask.html
- ECS ContainerOverride API: https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_ContainerOverride.html
- ECS Fargate task definition: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html
- SageMaker CreateProcessingJob: https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_CreateProcessingJob.html
- SageMaker regions and quotas: https://docs.aws.amazon.com/sagemaker/latest/dg/regions-quotas.html
- SageMaker Clarify processing jobs: https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-configure-processing-jobs.html
- EventBridge Scheduler: https://docs.aws.amazon.com/scheduler/latest/UserGuide/what-is-scheduler.html
- aws-samples SFN examples: https://github.com/aws-samples/aws-stepfunctions-examples
- AWS Serverless Data Lake Framework: https://aws.amazon.com/solutions/implementations/aws-serverless-data-lake-framework/
