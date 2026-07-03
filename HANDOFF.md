# Session handoff — model-monitor-custom

Last active: 2026-07-03. Written to survive a Claude restart. Read this file first when resuming.

## Where we are

Standalone R&D repo replacing SageMaker Model Monitor + Clarify with own-container / own-math batch analysis on ECS Fargate + Step Functions. Started as a worktree off `ml-iac`; now a full separate repo at `github.com/EoinMcUF/model-monitor-custom`.

**Phase status (per `docs/ROADMAP.md`):**

- ✅ Phase 0 — scaffolding
- ✅ Phase 1 — ABCs, schemas, fixtures, CI skeleton
- ✅ Phase 2 — 3 CDK stacks + base container + first analyser skeleton (wave 1) + SharedIamStack + bias skeleton (wave 2)
- ✅ Phase 3 — real BiasAnalyser (smclarify) + real ExplainAnalyser (SHAP) + OperationsBaselineStack + DQ/MQ/Shadow skeletons
- ✅ Phase 5 — real DqAnalyser (KS + PSI + completeness), real MqAnalyser (accuracy/F1/AUC + baseline compare), real ShadowAnalyser (agreement + JS divergence)
- ✅ Phase 6 — end-to-end runnable example at `examples/adult-classifier/`
- 🚫 Phase 7 — CDK Pipelines. **Deferred** per ROADMAP (gated on team-size 5+ or first prod deploy). Do not spawn without explicit user ask.

## What is on `main`

Last 17 commits are the delivery. `git log --oneline -20` on the repo shows the trail. Key artefacts:

| Path | What it is |
|---|---|
| `cdk/src/model_monitor_cdk/stacks/artifact_stack.py` | ECR + baselines bucket + KMS, per-consumer grants |
| `cdk/src/model_monitor_cdk/stacks/shared_iam_stack.py` | N reader roles + 1 writer role (per ADR 008) |
| `cdk/src/model_monitor_cdk/stacks/inference_monitor_stack.py` | Live analysis: Scheduler → SFN → 5 Fargate branches → DDB + Pipes |
| `cdk/src/model_monitor_cdk/stacks/operations_baseline_stack.py` | Snapshot analysis: S3 event → SFN → 5 Fargate branches → cross-account write |
| `containers/base/` | `mmc-base` shared library — env-var contract, ban-list guard, S3/DDB/CW clients, entrypoint, harness |
| `containers/bias/` | Real BiasAnalyser (smclarify wrapper, Adult parity test) |
| `containers/explain/` | Real ExplainAnalyser (SHAP + sklearn + XGBoost adapters) |
| `containers/dq/` | Real DqAnalyser (schema + completeness + KS + PSI drift) |
| `containers/mq/` | Real MqAnalyser (accuracy/precision/recall/F1 + baseline delta) |
| `containers/shadow/` | Real ShadowAnalyser (agreement + per-class disagreement + JS divergence) |
| `examples/adult-classifier/` | Runnable e2e — trains LR on Adult, drives all 5 analysers, writes `docs/e2e-output.md` + plots |
| `docs/design/001..008` | ADRs — IaC layout, container base, anti-SageMaker guardrails, schema evolution, observability, failure taxonomy, cross-account IAM |
| `.github/workflows/anti-sagemaker.yml` | CI grep-guard — fails PRs reintroducing SageMaker-shaped code |

**No worktrees. No open PRs. No open branches other than `main`.** Verify with `git worktree list` and `gh pr list`.

## Standing rules (do not violate on resume)

- **Draft PRs only.** Human flips ready. Never `gh pr ready` without user ask.
- **Never force-push to main.** Force-push only allowed on feature branches during rebase.
- **All squad paths under `~/UFX/`.** Worktrees at `<repo>/.worktrees/<slug>/`.
- **Always `uv run`** for python/pytest/ruff/ty. Never bare `python3`, `pip`, etc.
- **`claude-br` is a zsh alias**, not a real binary. When launching squads via `tmux send-keys`, either source the alias or expand it: `CLAUDE_CODE_USE_BEDROCK=1 AWS_PROFILE=DS AWS_REGION=eu-west-1 ANTHROPIC_MODEL=eu.anthropic.claude-opus-4-7 claude '<prompt>'`. Bare `claude-br` in a non-interactive shell will fail — bit us on Phase 6.
- **Squads post as the user in GH.** No "Squad θ says …" branding, no Claude scaffolding in PR bodies. Reviewers are UFX teammates, not you.
- **PR body is the rolling status, not comments.** No `SHEPHERD ROUND DONE` blocks in GH.
- **No SageMaker.** CI grep-guard enforces it. Excludes for legit-data files (`ban_list.py`, its test, ADR 003, research docs) already in the workflow.
- **Merge order matters when squads share `pyproject.toml` (root workspace).** First merges clean; subsequent squads need a rebase — trivial union merge on the `[tool.uv.workspace] members` + `dependencies` lists.

## Squad orchestration — how it went

Established pattern (repeat this on future waves):

1. Read ROADMAP + relevant ADRs to identify parallel work.
2. Analyse file-collision — only root `pyproject.toml` and `cdk/app.py` / `stacks/__init__.py` cross squads.
3. Cap wave at 4 build squads (heavy) or 6 skeletons (light).
4. Create worktrees at `.worktrees/<slug>/` with `feature/mmc-<slug>` branches.
5. Write `SQUAD_BRIEF.md` per worktree — mirrors the working ones from Phases 2/3/5/6. Read those for reference before writing new ones.
6. Spawn window `squad-mmc-<phase>` in tmux session `ufx`. Each pane runs `claude-br` (or expanded form) with a bootstrap prompt.
7. Cron-poll every 2 min. Approve `Do you want to proceed?` prompts with `1`+Enter.
8. Manager (this Claude) rebases when squads conflict on shared files, force-pushes, merges after CI green.
9. On merge: `git worktree remove .worktrees/<slug> --force`, delete local branch, `tmux kill-pane`.
10. When window empty, `tmux kill-window`. When phase done, `CronDelete <job-id>`.

## Known gotchas hit and mitigated

- **grep-guard trips on legit `ban_list.py`** — fix via pathspec excludes in `.github/workflows/anti-sagemaker.yml`. Already done for `containers/base/src/mmc_base/ban_list.py` + its test. Future analysers that reference the banned tokens as data (unlikely) need the same treatment.
- **Squads open PR while worktree not on latest main** → CONFLICTING on root `pyproject.toml`. Manager rebases in-place, fixes union of workspace members + deps, force-pushes.
- **`tmux send-keys "claude-br ..."` fails** because non-interactive shells don't load `.zshrc` aliases. Use expanded env-var form.
- **Squad prompts stack** — sometimes `Enter` sends prompt text into the pane's input buffer without submitting. Always follow `send-keys "text"` with `send-keys Enter` explicitly.

## Suggested next steps (in priority order)

Not started, up to user which direction:

1. **First real deploy** — the whole system is synth-clean, no CDK bootstrap has happened. Whoever picks this needs to decide target account, bootstrap CDK, push real ECR images (base + 5 analysers + baseline), then `cdk deploy` the stacks in order (Artifact → SharedIam → InferenceMonitor and/or OperationsBaseline). This unblocks Phase 7 (CDK Pipelines) and is the natural next milestone.

2. **Real training-data example.** Adult is a demo. A real UFX-shaped example — session-level data with real drift over time — would validate the analyser math beyond the fixture.

3. **`OperationsBaselineStack` cross-account event flow.** Current shape assumes same-account producer bucket. Real deployment will have the producer in `ml-data` and analysis running in `ml-operations` — needs EventBridge cross-account forwarding.

4. **Container image build + publish CI.** Right now Dockerfiles exist but nothing builds them. Add a GH Actions workflow that builds base + 5 analyser images, tags by git SHA, pushes to ECR on merge to main.

5. **Phase 7 (CDK Pipelines / GH Actions matrix)** — the ROADMAP gate is "team-size 5+ or first prod deploy." Currently 1 person, no prod. Ignore until (1) lands.

## Fastest-path bring-back prompt

Paste this at the top of the next session:

> Resume work on model-monitor-custom. Read /Users/eoinmca/UFX/model-monitor-custom/HANDOFF.md first — it has the full state, standing rules, and priorities. Confirm no dangling worktrees / open PRs / running crons before starting new work. Wait for a direction from me before spawning anything.

## Verification checklist for resume

```bash
cd ~/UFX/model-monitor-custom
git status                       # → clean on main
git worktree list                # → single row: repo root on main
gh pr list --state open          # → empty
tmux list-windows -t ufx 2>/dev/null | grep squad-mmc  # → empty
```

If any of those show unexpected state, investigate before spawning. If they're clean, you're at the exact spot this HANDOFF was written from.
