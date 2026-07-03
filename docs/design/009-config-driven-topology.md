# ADR 009 — Config-driven topology

## Context

The prototype originally routed stack instantiation through a
`-c target_account=<name>` context flag with three mutually-exclusive
`if target_account_name == ...` blocks in `cdk/app.py`. Placeholder
account IDs were literal in the file. To deploy the full topology into
one account (dev/collapse) you had to run `cdk deploy` three times with
three different context values. Adding a project meant editing `app.py`.

## Decision

The user declares topology in two YAML files:

- `environments/accounts.yaml` — `region`, `roles.artifact`,
  `roles.operations`, `roles.inference[]`, and an optional
  `operations_vpc_id`.
- `environments/projects.yaml` — one entry per model: `name`,
  `inference_account`, optional `schedule` and `vpc_id`.

`cdk/app.py` loads these via `model_monitor_cdk.config.EnvConfig` and
instantiates one `ArtifactStack`, one `SharedIamStack`, and per-project
`InferenceMonitorStack` + `OperationsBaselineStack` — each pinned to its
`cdk.Environment(account=..., region=...)`. CDK filters stacks by
`env.account` against the deploying profile, so
`cdk deploy '*' --profile <p>` acts on whatever the profile owns.
Single-account collapse works by listing the same 12-digit ID under all
three roles.

## Tradeoffs

- **No separate `topology=` flag.** The declarative shape carries the
  intent: repeating one ID across roles *is* the "collapsed" topology.
  A parallel flag would double the representation and drift.
- **Per-stack VPC is the default; `vpc_id` is opt-in.** Optional
  `vpc_id` on projects and `operations_vpc_id` on accounts let a
  collapsed topology share a pre-existing VPC via `Vpc.from_lookup`,
  without changing the default behaviour for multi-account setups.
