# Contributing to model-monitor-custom

Thanks for your interest! This is an active R&D project and contributions are welcome.

## Before you start

Read [`docs/STANDARDS.md`](docs/STANDARDS.md) first. We enforce:
- **TDD**: RED test before production code (enforced by pre-commit hook)
- **Type safety**: Pyright type checking on all Python
- **Code quality**: Ruff linting + formatting
- **Clean history**: Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, etc.)

## Quick contribution workflow

### 1. Set up your environment

```bash
git clone https://github.com/graylayer-labs/model-monitor-custom.git
cd model-monitor-custom
uv sync --group dev
```

### 2. Create a branch

```bash
git checkout -b feat/your-feature-name
# or
git checkout -b fix/issue-description
# or
git checkout -b docs/improvement-area
```

Branch naming: `{type}/{kebab-case-slug}`

### 3. Write a failing test first (RED)

For code changes to `src/` or CDK stacks, you must write a failing test first:

```bash
# Example: testing a new DynamoDB operation
cat > cdk/tests/test_new_feature.py << 'EOF'
def test_new_feature_does_something():
    # Arrange
    result = my_function()
    # Assert
    assert result == expected_value
EOF
```

Run it to see it fail:
```bash
uv run pytest cdk/tests/test_new_feature.py -v
```

### 4. Implement the feature (GREEN)

Write the minimal code to make the test pass:

```python
def my_function():
    return expected_value
```

Run the test:
```bash
uv run pytest cdk/tests/test_new_feature.py -v
```

### 5. Refactor (REFACTOR)

Improve the code while keeping tests passing:

```bash
uv run pytest  # Run all tests to ensure nothing broke
```

### 6. Run quality checks

Before committing:

```bash
# Lint + format
uv run ruff check --fix .
uv run ruff format .

# Type check
uv run pyright .

# Tests
uv run pytest cdk/tests/ containers/*/tests/

# End-to-end
python3 scripts/localstack-test-runner.py
```

### 7. Commit with conventional message

```bash
git add -A
git commit -m "feat(cdk): add new analyser stack

Add CloudFormation stack for the new spatial analyser.
Includes IAM permissions, Lambda role, and event subscriptions.

Includes 3 new tests:
- test_spatial_analyser_stack_synthesis()
- test_spatial_analyser_permissions()
- test_spatial_analyser_triggers()

Closes #42"
```

**Format:**
```
<type>(<scope>): <subject>

<body (optional)>

<footer (optional, e.g., Closes #123)>
```

**Types:** `feat`, `fix`, `refactor`, `test`, `docs`, `chore`  
**Scope:** area affected (`cdk`, `containers-bias`, `docs`, etc.)  
**Subject:** imperative mood, lowercase, < 72 chars, no period

### 8. Push to your fork

```bash
git push origin feat/your-feature-name
```

### 9. Open a PR

- Use the same conventional commit format as your title
- Describe what changed and why
- Link related issues: `Closes #123`
- Self-review the diff before requesting review
- Mark as Draft if still WIP

## Testing guidelines

### Unit tests (fast, isolated)

Test individual functions:
```python
# cdk/tests/test_config.py
def test_config_loads_yaml_correctly():
    config = Config.from_yaml("...")
    assert config.project == "test"
```

Run: `uv run pytest cdk/tests/ -v`

### Integration tests (medium speed, real services)

Test against moto-mocked AWS or real LocalStack:
```python
# cdk/tests/test_baseline_registry.py
@mock_aws
def test_ddb_writes_baseline_entry():
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = ddb.create_table(...)
    # Write to table
    assert table.scan()["Count"] == 1
```

Run: `uv run pytest cdk/tests/ -v -m integration` (if marked)

### E2E tests (slow, full stack)

Test the entire system locally:
```bash
python3 scripts/localstack-test-runner.py
```

Runs: LocalStack start → Infrastructure creation → Pytest → Cleanup  
Time: ~34s  
No AWS credentials needed.

## Code style

### Python

- **Line length:** 100 chars (configured in `ruff.toml`)
- **Imports:** Sort with `from __future__ import annotations` at top
- **Type hints:** Always include for function signatures
- **Comments:** Only explain *why*, not *what*. Code names explain what.

### Bad:
```python
def process(x):  # Process the data
    y = x + 1  # Add one
    return y
```

### Good:
```python
def increment_counter(value: int) -> int:
    # Increment to account for 1-based indexing in DynamoDB
    return value + 1
```

## Documentation

- **README.md** — Project overview, quick start, key features
- **docs/ARCHITECTURE.md** — System design, data contracts, subsystems
- **docs/LOCALSTACK_TESTING.md** — How to run tests locally
- **docs/STANDARDS.md** — Code standards, TDD, quality gates
- **docs/CONFIGURATION.md** — Environment setup, account topology
- **docs/design/** — Design decisions (ADRs)
- **Docstrings** — Only on public functions/classes; explain intent, not implementation
- **Commit messages** — Describe *why*, not *what*

## Debugging

### Tests fail locally but CI passes?

1. Ensure you're on the latest main:
   ```bash
   git fetch origin && git rebase origin/main
   ```

2. Sync Python deps:
   ```bash
   uv sync --group dev
   ```

3. Run the full test suite:
   ```bash
   python3 scripts/localstack-test-runner.py --verbose
   ```

### LocalStack tests are flaky?

LocalStack startup sometimes is slow. Increase the wait timeout:
```bash
python3 scripts/localstack-test-runner.py --verbose
```

Check LocalStack health manually:
```bash
curl http://localhost:4566/_localstack/health
```

### Pre-commit hook blocks you?

The `tdd-guard.sh` hook requires a failing test before editing `src/`. If you're sure you need to bypass it:
```bash
BYPASS_TDD_GUARD=1 git commit -m "..."
```

But you'll hear about it in review 😄

## Design principles

When adding features, keep these in mind:

1. **Schema-first** — JSON schemas decouple subsystems (see `shared/schemas/`)
2. **Serverless** — Lambda + EventBridge, no always-on compute
3. **Local-first** — Testable without AWS credentials
4. **Multi-account** — Configuration drives deployment topology
5. **Observable** — CloudWatch metrics, DynamoDB audit trail
6. **Minimal scope** — Don't refactor beyond what the task requires

## Getting help

- **Questions?** Open an issue with `question` label
- **Found a bug?** Open an issue with `bug` label + reproduction steps
- **Have an idea?** Start a discussion or open an issue with `enhancement` label
- **Need review?** @ someone in your PR

## Recognition

Contributions are valued! If your PR is merged:
- You'll be credited in commit history (visible on GitHub)
- Major contributions may be mentioned in [CHANGELOG](CHANGELOG.md) (if we keep one)
- You get the satisfaction of improving a production ML monitoring system 🎉

---

**Ready to contribute?** Pick an issue from [issues](https://github.com/graylayer-labs/model-monitor-custom/issues) marked `good first issue` or `help wanted`, or propose your own enhancement.

Thanks for building with us!
