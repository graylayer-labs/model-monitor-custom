# Design decisions

Canonical design decisions for `model-monitor-custom`. ADR-style.

## Rules

- **One decision per file.** Numbered. Immutable slug — never renumber.
- **Fixed shape:** Status · Context · Decision · Rationale · Alternatives rejected · Consequences · Related.
- **Superseding, not editing.** If a decision changes, add a new numbered doc and mark the old one `Status: Superseded by 00X`. Historical decisions stay readable.
- **Every design doc is linked from `docs/README.md`** so contributors find them without spelunking.

## Index

| # | Title | Status |
|---|---|---|
| [001](001-iac-layout.md) | IaC layout — 4 stacks, `cdk deploy --profile`, mono-repo | Accepted |
| [002](002-container-base.md) | Container base image + analyser pattern | Accepted |
| [003](003-anti-sagemaker-guardrails.md) | Anti-SageMaker guardrails (CI grep, ban-list, port headers) | Accepted |
| [004](004-schema-evolution.md) | Schema evolution — SemVer, additive-only, dual-emit on breaking | Accepted |
| [006](006-observability-contract.md) | Observability contract — CW namespace, DDB row shape, logging | Accepted |
| [007](007-failure-taxonomy.md) | Failure taxonomy — 6-way Outcome + orthogonal Severity, `failure.json` sidecar | Accepted |
| [008](008-cross-account-iam-style.md) | Cross-account IAM style — 1 role vs N roles | Accepted |
