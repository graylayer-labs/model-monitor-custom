# ADR 010 — Cross-account S3 event flow

## Status

Accepted.

## Context

The `OperationsBaselineStack` is triggered by S3 `Object Created` events on a producer-owned bucket. In real deployments the producer and the analysis stack often live in different AWS accounts (e.g. data account produces training snapshots; ops account runs the analyser Step Function). Same-account topology is a config-driven degenerate case, not the assumption.

Before this ADR, the stack imported the producer bucket by ARN and attached an EventBridge rule in the same account — silently broken for cross-account topologies (the event never reaches the ops account's bus).

## Decision

Split responsibility across two stacks and use the **default event bus** on both sides.

**Producer side — `ProducerEventsStack`** (new, opt-in):
- Deployed in the producer account when `projects.<p>.producer_account` is set and differs from `accounts.roles.operations`.
- Creates an EventBridge rule on the producer's default bus matching `aws.s3` + `Object Created` + configured bucket + prefix.
- Target is the operations account's default bus (`arn:aws:events:<region>:<ops-account>:event-bus/default`), invoked through a dedicated forwarder role trusted by `events.amazonaws.com`.

**Operations side — `OperationsBaselineStack` (existing, extended):**
- When `producer_account_id` is set and differs from the stack account, attaches a resource-based policy on the ops default bus granting `events:PutEvents` from `arn:aws:iam::<producer>:root`.
- The existing S3-shaped rule works unchanged — forwarded events preserve `source`, `detail-type`, and `detail`, so the same match pattern fires the SFN.

Same-account topology (`producer_account` unset or equal to ops): neither the new stack nor the bus policy is provisioned. Zero-diff for the common single-account collapse.

### Why the default bus (not a custom bus)

S3 emits events onto the account's default bus natively. Sending them to a custom bus first would require an extra forwarding rule on the default bus of the producer — one hop for nothing. The default bus on both sides is the shortest correct path.

### Why a stack in the producer account (not click-ops)

Keeping the forwarder in code makes the topology reproducible: `cdk deploy '*' --profile <producer>` provisions exactly what's needed and nothing else. Alternative (documented and manual `aws events put-rule` per bucket) drifts and is invisible to reviewers.

## Consequences

- `projects.yaml` gains an optional `producer_account: <12 digits>` per project.
- One additional stack per cross-account project: `MMC-<env>-ProducerEvents-<project>`, deployed with `--profile <producer-account>`.
- `OperationsBaselineStack` gains one `AWS::Events::EventBusPolicy` resource per cross-account project, on the default bus.
- No change to same-account deployments — validated by `test_no_bus_policy_when_producer_account_omitted` and `test_no_bus_policy_when_producer_account_matches_ops`.

## Related ADRs

- 001 — IaC layout
- 008 — cross-account IAM style (writer role, reader roles)
- 009 — config-driven topology
