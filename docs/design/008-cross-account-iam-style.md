# 008 — Cross-account IAM style

**Status:** Accepted.
**Related:** [`001-iac-layout.md`](001-iac-layout.md),
[`003-anti-sagemaker-guardrails.md`](003-anti-sagemaker-guardrails.md),
[`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## Context

`SharedIamStack` (home account `ml-artifact`, per ADR 001) mints the cross-account roles that let other accounts touch
the baselines bucket and its KMS key. The consumer set from the account topology in `ARCHITECTURE.md` is fixed and
small:

| Consumer account | Direction | What it needs |
|---|---|---|
| `ml-operations` | writer | `s3:PutObject` to baselines prefix + `kms:GenerateDataKey` on the artefact CMK |
| `ml-inference-test` | reader | `s3:GetObject` on baselines prefix + `kms:Decrypt` on the artefact CMK |
| `ml-inference-prod` | reader | same as test |
| `ml-dev` | reader | same as test (sandbox notebooks pull baselines to reproduce analyses locally) |

The writer is unambiguously its own role — write perms differ from read perms and only one account writes. The open
question is the **reader** grant: one role trusted by three principals, or three roles (one per consumer). Squad ζ
(SharedIamStack, Wave 2) needs the answer settled before it codes the construct — the shape leaks into stack outputs,
CloudTrail queries, and the per-consumer `Role.from_role_arn(...)` wiring in every `InferenceMonitorStack`.

## Decision

**N reader roles — one per consumer account. One writer role for `ml-operations`.**

Concretely, `SharedIamStack` takes a `reader_accounts: list[str]` prop and loops:

```
for account_id in reader_accounts:
    Role(
        self, f"BaselineReader-{account_id}",
        role_name=f"mmc-{env}-baseline-reader-{account_id}",
        assumed_by=AccountPrincipal(account_id),
        ...
    )
```

Each role's ARN is exported as a stack output keyed by account ID; each `InferenceMonitorStack` imports the ARN
for its own account and uses it as the Processing Job execution role's target for `sts:AssumeRole`.

The writer role (`mmc-<env>-baseline-writer`) is a single `Role` trusted by `ml-operations` — one writer, one role,
no fan-out to consider.

## Rationale

- **Blast radius scoped per consumer.** If credentials for the `ml-inference-test` session leak, the attacker gets
  test-tier read only — not prod, not dev. With one shared role, any session credential compromise reaches every
  consumer's read surface.
- **CloudTrail readability.** Role name carries the consumer account ID, so the `sessionIssuer.arn` field in a
  CloudTrail event answers "which consumer read this baseline?" without correlating source IPs or session tags.
  With one role the answer requires joining on `sourceIPAddress` and hoping VPC endpoints preserve it.
- **Clean consumer deletion.** Removing `ml-dev` from the reader set means deleting one CFN resource — trust policy
  on the surviving roles is untouched. With one role, every consumer removal is a trust-policy edit; every edit
  invalidates active session credentials for every other consumer at the moment CFN updates the policy.
- **Divergence-ready.** Prod will almost certainly need at least one permission the test tier does not — e.g. an
  extra prefix, a longer session duration, a tighter `aws:SourceVpce` condition. With N roles this is a policy
  attachment on one role; with one role it becomes conditionals inside a shared policy document.
- **IaC verbosity is negligible.** Three `Role` constructs inside a loop is not meaningfully more code than one
  `Role` with a three-element `Principal` list — and the loop is the same shape ζ already uses for per-consumer
  stack outputs.

### Threshold for flipping to one shared role

Flip to one-role-N-principals **only if all** of the following hold:

1. Consumer count > ~10 (the point at which CFN template size and stack-output plumbing become visible tax).
2. Permissions are provably identical across consumers and expected to stay identical (no per-tier divergence).
3. A separate detection control gives us the CloudTrail-per-consumer answer another way (e.g. session tagging on
   `sts:AssumeRole` with a mandatory `Consumer` tag, enforced by SCP).

At three consumers with likely test/prod divergence and no session-tag SCP in place, none of these hold. Revisit if
consumer count reaches double digits.

## Alternatives rejected

- **One role, N principals in trust policy.** Trust policy lists `ml-inference-test`, `ml-inference-prod`, `ml-dev`
  as `Principal`. One CFN resource; every trust-policy edit invalidates every consumer's active sessions; CloudTrail
  cannot distinguish which consumer used the role without extra tagging; blast radius is the union of all consumers.
  Rejected primarily on blast radius and audit readability; the IaC saving is not worth either.
- **One role, N principals + mandatory session tag.** Adds a `Consumer` session tag to the assume-role call and
  requires it via `aws:RequestTag`. Fixes the CloudTrail readability gap but keeps the blast-radius and
  trust-policy-churn problems. Also depends on every consumer's `AssumeRole` caller remembering the tag — a
  runtime discipline enforced by a `Deny` policy is more moving parts than one role per consumer.
- **RAM share on the bucket instead of assume-role.** Would replace the reader role entirely with a resource-based
  policy. Considered and rejected because the KMS CMK still needs a key policy grant per principal account, so we
  do not eliminate per-consumer IAM plumbing — we only relocate it to a less familiar surface (RAM + key policy)
  for no blast-radius win.
- **Push baselines into each consumer account.** Removes the cross-account read entirely. Rejected in ADR 001 —
  `ml-artifact` is the single source of truth; duplicating baselines into every consumer breaks that invariant and
  multiplies storage and KMS cost.

## Consequences

- `SharedIamStack` exposes reader role ARNs as a `dict[str, str]` (account ID → role ARN) stack output.
  `InferenceMonitorStack` looks up its own account's ARN at synth time.
- Adding a consumer = one PR that (a) adds the account ID to the `reader_accounts` prop, (b) adds a lookup in the
  new consumer's `InferenceMonitorStack`. No trust-policy edit on existing roles; existing consumers see no churn.
- Removing a consumer = delete the entry in `reader_accounts`. CFN removes exactly one `Role` resource; other
  consumers unaffected.
- CloudTrail queries on baseline reads can group by role ARN to answer "which account read which baseline when."
  Dashboards in Wave 3 can lean on this without needing session tags.
- If the consumer set later grows past ~10 or session-tagging is mandated org-wide, re-open this ADR as `009` per the
  supersede rule in `README.md` — do not edit this one.
- `mmc-<env>-baseline-writer` stays a single role because there is one writer principal (`ml-operations`). If a second
  writer ever appears, apply the same rule as readers: a role per writer account.
