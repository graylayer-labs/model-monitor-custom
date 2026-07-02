# Standards

Non-negotiables for `model-monitor-custom`. Every PR is measured against these.

## Language + versions

- **Python 3.12** target across the workspace. Nothing older.
- **Type annotations required** on every public function, method, and class. Missing annotations = failing type-check.
- **Google-style docstrings** on every public function, method, and class. No exceptions.
- **Line length 120.**

## Tooling

Managed via `uv` at the workspace root. Never call bare `python`, `pip`, `.venv/bin/*`, or `pytest`.

| Tool | Purpose | Command |
|------|---------|---------|
| `ruff format` | Formatter (replaces black + isort) | `uv run ruff format <path>` |
| `ruff check --fix` | Linter (autofix where safe) | `uv run ruff check --fix <path>` |
| `ty check` | Type checker | `uv run ty check <path>` |
| `pytest` | Test runner + coverage | `uv run pytest` |

No pyright. `ty` is the single type checker. When it disagrees with older docs online, `ty` wins.

Rules in effect (from `ruff.toml`): `A ANN B BLE C4 C90 COM D DOC DTZ E ERA F I ISC N PERF PL PTH RET RSE RUF S SIM T TID TRY UP W`.

## TDD, always

- **RED first.** A failing test exists before a line of production code lands. If you cannot write a failing test, the task is not well-defined; go redefine it.
- **One test file per source file.** Path mirrors source: `containers/baseline/src/model_baseline/analyzers/bias.py` → `containers/baseline/tests/analyzers/test_bias.py`.
- **Test level coverage** — every level:
  - **Unit tests** — pure logic, no I/O, no network, no filesystem. Fast (< 100 ms per test).
  - **Integration tests** — module boundary crossings. Real filesystem in a tmp dir, mocked S3, mocked SageMaker.
  - **End-to-end tests** — real container built + run against a fixture dataset + a fixture model artefact. Slow, gated behind an explicit marker (`@pytest.mark.e2e`).
- **Fixtures are code.** Every fixture dataset lives under `tests/fixtures/` with a short README describing schema + provenance. Never a binary blob without a reproduction script.
- **Coverage floor: 90% lines, 85% branches**. Coverage checked in CI. Missing tests = failing gate.

## Complexity

- **McCabe ≤ 10 per function.** Higher = split it.
- **Flat over nested.** Invert conditions, return early. Guard clauses.
- **No unnecessary wrappers.** If a function just calls another with the same signature, delete it.
- **No speculative abstractions.** YAGNI — do not generalise without two concrete use cases.

## Documentation

- **README per package.** `containers/monitor/README.md`, `containers/baseline/README.md`, `cdk/README.md`. Each has: what it does, quickstart, config surface, error catalogue.
- **ARCHITECTURE.md at root** — how the pieces fit. Kept in sync with code by policy; drift = bug.
- **Design docs in `docs/`** — long-form. Don't restate types or config schemas; those live near the code and stay authoritative.

## Config immutability

- **Never weaken lint / type / test config to make a change land.** Fix the code. If a rule is wrong for the project, propose a change to `ruff.toml` in its own PR with rationale.

## Errors, logging, observability

- **Structured JSON logs** to stdout. One log line = one JSON object. No plain-text `print()` in production code.
- **Errors halt loudly.** Never swallow an exception. When we recover, we log the recovered failure explicitly.
- **Emit a `failure.json` sidecar** to the same S3 output prefix on any hard failure, containing the exception class, message, full traceback, environment snapshot, and container image digest. This is a direct response to the opaqueness of SageMaker Clarify's error surface.

## Security

- **No secrets in code or commits.** No credentials, API keys, tokens.
- **AWS named profiles only.** Never ambient credentials.
- **IAM stays minimal.** Every construct's IAM policy is scoped to specific resource ARNs; no `*` unless a test proves narrower doesn't work and the reasoning is documented in a code comment.
- **Cross-account grants are explicit inputs to constructs**, not hardcoded account IDs. See `ARCHITECTURE.md` for the account topology this project assumes.

## Commits + PRs

- **Conventional Commits.** `feat|fix|refactor|perf|style|test|docs|build|ops|chore`. Scope lowercase + hyphens.
- **Draft PRs only** until CI + self-review are green. No "ready for review" without the gate passing.
- **No force-push to `main`.** Feature branches only.
- **One logical change per commit.** Rebase locally, keep history clean.

## Cross-account posture

- The project assumes an AWS multi-account layout mirroring AWS ML best practice (see `ARCHITECTURE.md`).
- **Even when deploying into a single account for prototype**, all constructs take account IDs and role ARNs as inputs — never read the current account and assume co-location. This keeps the single-account and multi-account topologies identical in code shape.

## Contributor checklist (per PR)

1. RED test written first?
2. `uv run ruff format .` clean?
3. `uv run ruff check .` clean?
4. `uv run ty check .` clean?
5. `uv run pytest` green + coverage floors met?
6. Docstrings on every new public symbol?
7. Error paths log structured + emit sidecar where relevant?
8. Any hardcoded account IDs or role ARNs? (If yes: fix.)
9. Any `*` in an IAM policy? (If yes: justify in comment or narrow.)

All nine must be `yes` (or `n/a` with reason) before merge.
