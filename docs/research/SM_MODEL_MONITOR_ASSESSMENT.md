# SageMaker Model Monitor + Clarify — honest assessment vs. custom monitoring

> Internal engineering assessment. Author: Eoin (`@<author>`). Date: 2026-07-02.
> Companion doc: [`MONITORING_CUSTOM_CONTAINER.md`](MONITORING_CUSTOM_CONTAINER.md) — describes the `model-monitor` container we already ship for MQ/DQ.
> This doc is `main`-branch-appropriate. The custom `model-monitor` container it references lives on `feature/monitor-container` at commit [`570a7899`](https://github.com/<deploy-repo>/tree/570a7899a08696c2495d44589d07ab076cb3efbb/containers/model_monitor).

---

## §1 — Executive summary

- **What I tried.** Wire SageMaker Model Monitor (MQ + DQ) and Clarify (bias + explainability) end-to-end for `example_classifier`, using AWS-managed baseline Processing Jobs cross-account into `ML_ARTIFACT`.
- **What I hit.** Days lost on opaque Clarify config validation (`analyzer/config/jobconfig.py:129 Unexpected output in the processing job config`), a hard un-adjustable 1 rps `CreateProcessingJob` platform limit, three bespoke cross-account IAM/KMS/S3 grants, a dormant upstream repo, and — as of the writing of this doc — **AWS has publicly closed new customer access to SageMaker Clarify effective 2026-06-30 and now recommends `shap` + pandas/scikit-learn + AWS-samples reference solutions instead**. That is the strongest possible signal: the platform we've been fighting has been officially superseded by its owner.
- **What I recommend.** Detach the last piece we still take from Model Monitor / Clarify — baseline computation — and put it in a small `model-baseline` container that runs beside our existing `model-monitor` container. ~250-400 LOC. Cost: ~1 squad-day. Removes every one of the pain points below and puts us exactly on the pattern AWS itself is now telling customers to use.

---

## §2 — What SageMaker Model Monitor + Clarify actually give you

Two stacks, often conflated:

**SageMaker Model Monitor**

- Baseline compute (Processing Job) that produces `constraints.json` + `statistics.json` from a training snapshot.
- Scheduled Processing Jobs that compare captured inference traffic against the baseline and emit `constraint_violations.json`.
- Wrappers for four monitor types: Data Quality, Model Quality, Bias Drift, Feature Attribution Drift.

**SageMaker Clarify**

- One-shot bias + SHAP explainability Processing Jobs on training data + a live/shadow endpoint.
- Produces `analysis.json` consumed by the two "drift" variants of Model Monitor above.

Value proposition on paper: turnkey, AWS-native, compliance-friendly, integrated with MPG and Endpoint capture. In practice the machinery is a thin scheduler on top of two closed-source container images (Model Monitor and Clarify) with a lot of undocumented shape constraints — see §3.

Our existing custom container [`model-monitor`](https://github.com/<deploy-repo>/tree/570a7899a08696c2495d44589d07ab076cb3efbb/containers/model_monitor) already consumes `constraints.json` / `statistics.json` / `analysis.json` and emits CW metrics. It is deliberately schema-compatible so that whichever thing produces those JSON files (Clarify today, our own container tomorrow) is swappable.

---

## §3 — Where the design breaks down for us

Each item below has evidence — an AWS URL, a verbatim error, or a commit SHA.

### 3a. Closed-source container internals

`analyzer/config/jobconfig.py:129` is inside the AWS-managed Clarify container image. The public `aws/amazon-sagemaker-clarify` repo ships `smclarify/bias/` and `smclarify/util/` — it does **not** ship the `analyzer/` package that the Processing Job actually runs. So the file path in the error is unreachable to us; the only feedback is the string itself.

Zero StackOverflow or GitHub-issue results for the exact error `Unexpected output in the processing job config`. The AWS troubleshooting page ([Troubleshoot SageMaker Clarify Processing Jobs](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-processing-job-run-troubleshooting.html)) enumerates ~9 error classes; this string is not among them. The catch-all for our case there is "Analysis configuration schema validation error" — which does not fire for us; the schema validator passes and the failure is a deeper unenumerated check.

### 3b. Undocumented / poorly-documented required shapes

Enumerated from actual iterations this week (`feature/monitor-container` branch, SFN executions in `ML_ARTIFACT`):

- Input JSONL records for Clarify must be nested `{"features": [...], "label": ...}`. Flat top-level scalars are not marked unsupported anywhere obvious, and silently fail baseline compute. Every AWS example notebook uses the nested shape.
- `dataset_type: application/jsonlines` requires `features` field JMESPath. `"*"` is valid JMESPath but rejected by the container.
- `headers` list must end with the label column. Order-sensitive, undocumented at the schema level (only surfaces in the troubleshooting page under "Headers must contain label").
- `content_template` for the SHAP predictor must literally contain `$features` — no other placeholder name works, no explicit error naming the missing token.
- SHAP `baseline` field: single-element list of dicts of the form `[{"features": [values]}]`. The intuitively-correct `[{col: val, ...}]` shape validates against the schema but fails at compute time.
- `label_headers` semantics differ between multiclass-with-integer output and named-class output; not documented, only inferable from source in [`smclarify/bias/report.py`](https://github.com/aws/amazon-sagemaker-clarify/blob/master/src/smclarify/bias/report.py).
- Nested-JMESPath extraction from our model response (`details.extra.class_probabilities.Bot`) is not covered by any AWS sample notebook. Every AWS example returns a flat top-level probability.

These are individually small. Combined, iteration is trial-and-error against opaque failures — see 3e.

### 3c. API rate limits

Per [AWS SageMaker service quotas](https://docs.aws.amazon.com/general/latest/gr/sagemaker.html):

> Rate of CreateProcessingJob requests — Each supported Region: 1 — Adjustable: **No**.

Hard 1 rps ceiling that cannot be raised. Bias + explainability + data-quality baseline for N models × EventBridge fan-out × SFN retries hits `ThrottlingException` easily. We already saw this on repeated SFN executions this week during Clarify config iteration.

### 3d. Cross-account topology

Baselines bucket is in `ML_ARTIFACT` (`965377249924`). Model MPG for `example_classifier` is in `DS` (`714462557551`) today (migration TBD — see SYSTEM `Known Gaps`). Clarify's shadow endpoint for SHAP must live in the same account as the Clarify Processing Job. Cross-account plumbing needs at minimum:

1. S3 bucket policy grant on the baselines bucket to the Clarify execution role.
2. KMS key policy grant on the artifact CMK.
3. MPG resource policy grant + role trust adjustment for cross-account model reference.

DS is largely un-CDK-managed, so (1)-(3) require manual DS-side action per model. Each is a bespoke ticket. None of this scales to 4 models × 3 monitors × 2 accounts.

### 3e. Feedback loop

- No dry-run for Clarify config. Every schema/shape change requires launching a full Processing Job.
- 5-15 min per iteration before the error surfaces in CW.
- SFN retry policy compounds wall-clock time.
- Errors are opaque strings from a closed-source container.

Net effect: multiple engineering days on config iteration alone.

### 3f. Community + maintenance signal

- Upstream repo [`aws/amazon-sagemaker-clarify`](https://github.com/aws/amazon-sagemaker-clarify): **0 open PRs**, 4 open issues (most recent Sep 2024, `#145 License issue` and Jul 2024 `#144`). Issue creation is restricted on the repo.
- No re:Invent 2024/2025 keynote features led with Clarify.
- No public writeup I could find of any FAANG-scale team standardising on SageMaker Model Monitor + Clarify (see §4).
- **AWS themselves have closed new customer access to Clarify effective 2026-06-30**, per the [Clarify availability change](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html) notice:

  > After careful consideration, we have made the decision to close new customer access to Amazon Sagemaker Clarify, effective 6/30/26. Existing customers can continue to use the service as normal. AWS continues to invest in security and availability improvements for Clarify, but we do not plan to introduce new features.

  Same page recommends replacement stack: [aws-samples/sample-aiops-on-amazon-sagemakerai monitoring reference](https://github.com/aws-samples/sample-aiops-on-amazon-sagemakerai/tree/main/monitoring) + the `shap` library directly + pandas/scikit-learn for the standardised bias formulas + SageMaker-managed MLflow + CloudWatch + QuickSight. This is precisely the DIY-container pattern §4 describes.

That is the single strongest signal in this doc. AWS's own guidance is now: don't build new on Clarify — compute the bias metrics yourself, use `shap` directly, run it in your own SageMaker Processing Job / Pipelines step, log to MLflow.

---

## §4 — What everyone else actually does

I could not find a single public writeup from a comparable ML team standardising on SageMaker Model Monitor + Clarify at scale. Every public writeup I found — FAANG-scale ML platforms and ML-observability vendors — ships some variant of the same pattern:

1. Custom container running on their own compute (K8s / Argo / Airflow / ECS / Batch / Lambda) — **not** SageMaker Model Monitor.
2. `shap` (or occasionally `captum` / `alibi`) for feature attribution.
3. `evidently` or `whylogs` or in-house math for drift + statistical checks.
4. Batch schedule via own orchestrator (Argo cron, Airflow, EventBridge → SFN → own container).
5. Metrics emitted to their existing observability stack (Datadog / Prometheus / CloudWatch).

| Team / product | Monitoring stack | Explainability | Drift | Source |
|---|---|---|---|---|
| Uber Michelangelo | Internal platform, logs predictions + joins outcomes for accuracy; distribution comparison for feature/prediction shift | Built-in visualisation, no third-party lib named | In-house | [Meet Michelangelo (Uber blog)](https://www.uber.com/blog/michelangelo-machine-learning-platform/) |
| Netflix | In-house ML observability platform. Identifies data drift, model degradation, operational issues. Payments and beyond. | In-house dashboards on Metadata Service / Model Lifecycle Graph | In-house | [ML Observability: Bringing Transparency to Payments and Beyond](https://netflixtechblog.com/ml-observability-bring-transparency-to-payments-and-beyond-33073e260a38); [Democratizing ML at Netflix — Model Lifecycle Graph](https://netflixtechblog.com/democratizing-machine-learning-at-netflix-building-the-model-lifecycle-graph-5cc6d5828bb1) |
| Airbnb Chronon (fka Zipline) | Feature platform with auto-generated monitoring pipelines + feature observability. Training-serving consistency baked in. | In-house | Native — drift + training/serving skew | [Chronon — Airbnb Engineering](https://airbnb.tech/opensource/chronon/); [airbnb/chronon on GitHub](https://github.com/airbnb/chronon); [Chronon at QCon SF 2023](https://qconsf.com/presentation/oct2023/chronon-airbnbs-end-end-feature-platform) |
| DoorDash | Custom drift monitoring for input + output distributions; performance measured vs. real-world data | In-house | Native | [Maintaining ML Model Accuracy Through Monitoring — DoorDash](https://careersatdoordash.com/blog/monitor-machine-learning-model-drift/); [DoorDash monitoring tag](https://careersatdoordash.com/blog/tag/monitoring/) |
| WhyLabs / whylogs | Open-source data logging + drift; ships profiles, integrates with hosted WhyLabs | Ecosystem: pair with `shap` | Native | [whylabs/whylogs](https://github.com/whylabs/whylogs) — 2.8k★, active |
| Evidently AI | Open-source Python framework, 100+ built-in metrics (drift, data quality, classification, LLM eval) | Ecosystem: pair with `shap` | Native | [evidentlyai/evidently](https://github.com/evidentlyai/evidently) — 7.7k★, 2,795 commits, latest release 2026-03 |
| Arize / Fiddler / Aporia | Managed ML observability vendors — SDK to instrument, they host drift + attribution dashboards | Native (SHAP-family) | Native | [Arize model monitoring](https://arize.com/model-monitoring/). No public migration-off-Clarify case study surfaced in search — vendor comparison pages only ([SageMaker Clarify vs Arize](https://sourceforge.net/software/compare/Amazon-SageMaker-Clarify-vs-Arize-AI/)). Reported as-is; no fabricated case study. |
| AWS-samples reference (2026) | AWS's own recommended replacement for Clarify: SageMaker Pipelines + Processing step + `shap` + pandas + MLflow + Athena/QuickSight | `shap` directly | Directly-computed standardised metrics (DPL, DPPL, DI, CI) via pandas/scikit-learn | [aws-samples/sample-aiops-on-amazon-sagemakerai/monitoring](https://github.com/aws-samples/sample-aiops-on-amazon-sagemakerai/tree/main/monitoring) |

Key implication: even AWS's own 2026 reference architecture for what to build now that Clarify is being wound down is a **custom Processing container** driven by `shap` + pandas — not another AWS-managed monitor.

---

## §5 — Where this project is on this map

We already have:

- `model-monitor` container ([`containers/model_monitor/`](https://github.com/<deploy-repo>/tree/570a7899a08696c2495d44589d07ab076cb3efbb/containers/model_monitor)) — consumes baseline JSONs from S3, computes drift + emits CW metrics. That is the industry pattern.
- `MonitoringScheduleStack` + `MonitoringStack` — EventBridge → SFN → our container in `ML_ARTIFACT`.
- DDB outcomes table + EventBridge Pipes fan-out for ground truth join.
- Dashboards + CW alarms.

The **only** piece still coupled to Model Monitor / Clarify is baseline compute (Clarify Processing Job producing `constraints.json` / `statistics.json` / `analysis.json`). MQ and DQ Model Monitor schedules — the scheduled comparison side — we've already replaced with `model-monitor`.

We are ~80% on the industry pattern. Detaching the last piece takes us to 100%, matches AWS's own 2026 recommendation, and removes every pain point in §3.

---

## §6 — Recommendation

Build `model-baseline` — a peer container to `model-monitor`, same repo, same invocation topology (SFN + EventBridge in `ML_ARTIFACT`, image published to the same ECR).

**Inputs**

- Training snapshot (S3, from train-repo Feature Store export) — same input Clarify takes today.
- Config JSON: label column, facet column, target facet value, class labels, N SHAP samples.

**Outputs**

- `analysis.json` at the S3 path our `model-monitor` container already reads. Same schema as Clarify emits, so downstream code is unchanged.
- `constraints.json` / `statistics.json` for MQ / DQ variants — the same statistical formulas Clarify runs, computed with pandas.

**Libraries**

- [`smclarify.bias.report.bias_report`](https://github.com/aws/amazon-sagemaker-clarify/blob/master/src/smclarify/bias/report.py) — the open-source part of Clarify. This gives us CI / DPL / KL / JS in ~5 LOC. `smclarify` remains pip-installable regardless of the Clarify service closure — the metrics are just formulas.
- [`shap.KernelExplainer(model, data, ...)`](https://shap.readthedocs.io/en/latest/generated/shap.KernelExplainer.html) — same engine Clarify uses internally per AWS's own [Clarify SHAP values doc](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-shapley-values.html). Call `predict_proba` on the loaded model directly (no shadow endpoint). ~50 LOC.
- pandas / scikit-learn — standardised bias formulas per AWS's replacement guidance.

**Size**

~250-400 LOC total: driver, config parser, bias driver, SHAP driver, JSON emitter, Dockerfile, entrypoint. Peer in size to `model-monitor`.

**Deployment**

- New CDK construct `BaselineConstruct` in `deploy-repo`. Rip the Clarify branch out of `AnalyzerBaselineConstruct` and route to the new construct.
- No cross-account SHAP endpoint. Model loaded in-container from `model.tar.gz` — either post-migration to `ML_ARTIFACT` MPG (see SYSTEM Known Gaps), or a one-time S3 copy for the pilot.
- Same ECR + SFN + EventBridge invocation topology `model-monitor` already uses.

**Cost**

~1 squad-day work, incl. Dockerfile, driver, unit tests, CDK wiring, and a one-shot proof on `example_classifier`.

**Benefits (each fixes a §3 item)**

- 3a → We own the code. No closed-source container.
- 3b → Config is our own JSON schema, dry-runnable, unit-testable.
- 3c → No `CreateProcessingJob` per baseline — we can batch and control retry policy, and we can run on any compute we choose (Processing Job, Batch, Lambda).
- 3d → No cross-account shadow endpoint. One IAM role in `ML_ARTIFACT`.
- 3e → Local dry-run. Full unit tests. Feedback in seconds, not minutes.
- 3f → We track upstream `smclarify` and `shap` — both actively maintained (`smclarify` remains AWS's own reference implementation of the bias formulas; `shap` is the same engine Clarify uses internally).

---

## §7 — Migration plan (high-level)

Not this doc's job to design. Sketch only, so leadership sees the shape:

1. Register `example_classifier` (latest MP version) into `ML_ARTIFACT` MPG so DIY compute can load the model locally without cross-account round-trips. (Blocked on / gated by SYSTEM Known Gap: DataScience account migration — 3 of 4 projects still training in `DS`.)
2. Author `model-baseline` container (bias driver + SHAP driver + JSON emitter). Same repo, same CI, same ECR as `model-monitor`.
3. Rip the Clarify branch out of `AnalyzerBaselineConstruct` in `deploy-repo`. Replace with `BaselineConstruct` referencing the new image. Keep the Model-Monitor MQ / DQ baseline branches intact for the interim if we want, or fold those into `model-baseline` at the same time.
4. Proof end-to-end on `example_classifier`: run baseline → drop into monitoring S3 path → confirm existing `model-monitor` schedule consumes it and emits identical CW metrics.

Sequence and sizing owned by ML platform lead — not scoped here.

---

## §8 — What we keep

Explicit non-goals of this proposal. We are **not** rebuilding:

- `model-monitor` container ([`MONITORING_CUSTOM_CONTAINER.md`](MONITORING_CUSTOM_CONTAINER.md)) — already the industry pattern.
- `MonitoringScheduleStack` — EventBridge + SFN schedule that fires the monitor container.
- `MonitoringStack` — dashboards, alarms, DDB outcomes table.
- EventBridge Pipes fan-out for ground truth join.
- CloudWatch dashboards + alarms.
- Bedrock guardrail for LLM explanation surface ([#204](https://github.com/<deploy-repo>/pull/204)).

All of the above stays. The change is bounded to the baseline computation side.

---

## §9 — Appendix: raw error dumps

For anyone (including future-me) googling these strings in a year — pasted verbatim so search engines index them and land you here.

```
analyzer/config/jobconfig.py:129 Unexpected output in the processing job config
```

```
[TODO: verbatim quote from CW logs — Clarify SHAP baseline shape error from SFN execution 2026-06-xx in ML_ARTIFACT]
```

```
[TODO: verbatim quote from CW logs — Clarify JMESPath features-field validation error, same SFN run]
```

```
[TODO: verbatim quote from CW logs — ThrottlingException on CreateProcessingJob from parallel bias+explainability fan-out]
```

Fill-ins to come from the SFN executions already captured this week — flagged so the Manager knows which project incident refs to slot in.

---

## References (used above)

- AWS — [Clarify availability change (deprecation notice)](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html)
- AWS — [Troubleshoot SageMaker Clarify Processing Jobs](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-processing-job-run-troubleshooting.html)
- AWS — [SageMaker service quotas (`CreateProcessingJob` rate)](https://docs.aws.amazon.com/general/latest/gr/sagemaker.html)
- AWS — [aws-samples/sample-aiops-on-amazon-sagemakerai — monitoring reference](https://github.com/aws-samples/sample-aiops-on-amazon-sagemakerai/tree/main/monitoring)
- AWS — [aws/amazon-sagemaker-clarify](https://github.com/aws/amazon-sagemaker-clarify) (upstream repo)
- AWS — [`smclarify.bias.report.bias_report` source](https://github.com/aws/amazon-sagemaker-clarify/blob/master/src/smclarify/bias/report.py)
- AWS — [Clarify SHAP values doc (confirms shap is the engine)](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-shapley-values.html)
- SHAP — [`shap.KernelExplainer` API](https://shap.readthedocs.io/en/latest/generated/shap.KernelExplainer.html)
- Evidently — [evidentlyai/evidently](https://github.com/evidentlyai/evidently)
- WhyLabs — [whylabs/whylogs](https://github.com/whylabs/whylogs)
- Uber — [Meet Michelangelo](https://www.uber.com/blog/michelangelo-machine-learning-platform/)
- the project internal — [`docs/MONITORING_CUSTOM_CONTAINER.md`](MONITORING_CUSTOM_CONTAINER.md)
- the project internal — [`feature/monitor-container@570a7899` — `model-monitor` source](https://github.com/<deploy-repo>/tree/570a7899a08696c2495d44589d07ab076cb3efbb/containers/model_monitor)
