#!/usr/bin/env python3
"""
LocalStack end-to-end test runner.

Single-command orchestration for repeatable LocalStack testing:
1. Verify LocalStack is running (or start it)
2. Build required Docker images
3. Provision AWS resources (KMS, S3, DynamoDB, IAM, Lambda)
4. Deploy CDK infrastructure (OperationsBaselineStack + InferenceMonitorStack)
5. Run baseline E2E flow (LoadAndGate → Analysers → EvaluateResults → WriteRegistry)
6. Run monitor E2E flow (Activation → Analysers → Outcomes)
7. Report results
8. Clean up

Usage:
    python scripts/localstack-test-runner.py [--no-cleanup] [--verbose]

Exit codes:
    0 = all tests passed
    1 = LocalStack setup failed
    2 = Docker image build failed
    3 = CDK bootstrap/deploy failed
    4 = test execution failed
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


class LocalStackTestRunner:
    """Orchestrates LocalStack E2E test lifecycle."""

    def __init__(self, repo_root: Path, verbose: bool = False, cleanup: bool = True):
        """Initialize runner.

        Args:
            repo_root: Repository root directory
            verbose: Print detailed output
            cleanup: Tear down LocalStack after tests
        """
        self.repo_root = repo_root
        self.verbose = verbose
        self.cleanup = cleanup
        self.compose_file = repo_root / "docker-compose.localstack.yml"
        self.region = "eu-west-1"


    def log(self, msg: str, level: str = "INFO"):
        """Print log message."""
        prefix = f"[{level}]" if level != "INFO" else "[•]"
        print(f"{prefix} {msg}")

    def log_verbose(self, msg: str):
        """Print verbose log (only if verbose flag set)."""
        if self.verbose:
            self.log(msg, "DEBUG")

    def log_error(self, msg: str):
        """Print error message."""
        self.log(msg, "ERROR")

    def log_success(self, msg: str):
        """Print success message."""
        self.log(msg, "✓")

    def run_cmd(
        self,
        cmd: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run shell command with logging.

        Args:
            cmd: Command list
            cwd: Working directory
            env: Environment variables (merged with os.environ)
            check: Raise CalledProcessError on non-zero exit

        Returns:
            CompletedProcess result
        """
        if env is None:
            env = os.environ.copy()
        else:
            env = {**os.environ, **env}

        self.log_verbose(f"Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            cwd=cwd or self.repo_root,
            env=env,
            capture_output=True,
            text=True,
        )

        if check and result.returncode != 0:
            self.log_error(f"Command failed with exit code {result.returncode}")
            if result.stdout:
                print("STDOUT:", result.stdout[-1000:] if len(result.stdout) > 1000 else result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr)
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                result.stdout,
                result.stderr,
            )

        return result

    def is_localstack_healthy(self) -> bool:
        """Check if LocalStack is healthy."""
        try:
            result = subprocess.run(
                ["curl", "-f", "-s", "http://localhost:4566/_localstack/health"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode != 0:
                return False
            health = json.loads(result.stdout)
            s3_status = health.get("services", {}).get("s3")
            return s3_status in ("running", "available")
        except Exception:
            return False

    def start_localstack(self):
        """Start LocalStack container."""
        self.log("Starting LocalStack...")

        if self.is_localstack_healthy():
            self.log_success("LocalStack already running")
            return

        self.run_cmd(
            ["docker", "compose", "-f", str(self.compose_file), "up", "-d"],
            cwd=self.repo_root,
        )

        # Wait for health
        self.log("Waiting for LocalStack to become healthy...")
        max_attempts = 60
        for attempt in range(max_attempts):
            if self.is_localstack_healthy():
                self.log_success("LocalStack is healthy")
                return
            time.sleep(1)

        raise RuntimeError("LocalStack did not become healthy within 60s")

    def stop_localstack(self):
        """Stop LocalStack container."""
        if not self.cleanup:
            self.log("Skipping LocalStack teardown (--no-cleanup)")
            return

        self.log("Stopping LocalStack...")
        self.run_cmd(
            ["docker", "compose", "-f", str(self.compose_file), "down", "-v"],
            cwd=self.repo_root,
            check=False,
        )
        self.log_success("LocalStack stopped")

    def build_docker_images(self):
        """Build required Docker images from repo root (workspace context)."""
        self.log("Building Docker images...")

        # Build base Dockerfile.lambda
        base_dockerfile = self.repo_root / "containers" / "base" / "Dockerfile.lambda"
        self.log("Building mmc-base-lambda image...")
        self.run_cmd(
            [
                "docker",
                "build",
                "-f",
                str(base_dockerfile),
                "-t",
                "mmc-base-lambda:latest",
                str(self.repo_root),  # Build from repo root for workspace context
            ],
            cwd=self.repo_root,
        )
        self.log_success("Built mmc-base-lambda:latest")

        # Build analyser images
        for analyser in ["mq", "dq", "bias", "explain", "shadow"]:
            analyser_dockerfile = self.repo_root / "containers" / analyser / "Dockerfile.lambda"

            if not analyser_dockerfile.exists():
                self.log_verbose(f"Skipping {analyser} (Dockerfile.lambda not found)")
                continue

            self.log(f"Building mmc-{analyser}-lambda image...")
            self.run_cmd(
                [
                    "docker",
                    "build",
                    "-f",
                    str(analyser_dockerfile),
                    "-t",
                    f"mmc-{analyser}-lambda:latest",
                    "--build-arg",
                    "BASE_IMAGE=mmc-base-lambda:latest",
                    str(self.repo_root),  # Build from repo root for workspace context
                ],
                cwd=self.repo_root,
            )
            self.log_success(f"Built mmc-{analyser}-lambda:latest")


    def set_env_vars(self):
        """Set environment variables for test execution."""
        os.environ["AWS_ACCESS_KEY_ID"] = "test"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
        os.environ["AWS_DEFAULT_REGION"] = self.region

    def get_cdk_env(self) -> dict[str, str]:
        """Get environment for CDK commands."""
        return {
            "AWS_ENDPOINT_URL_S3": "http://localhost:4566",
            "AWS_ENDPOINT_URL_DYNAMODB": "http://localhost:4566",
            "AWS_ENDPOINT_URL_STEPFUNCTIONS": "http://localhost:4566",
            "AWS_ENDPOINT_URL_LAMBDA": "http://localhost:4566",
            "AWS_ENDPOINT_URL_IAM": "http://localhost:4566",
            "AWS_ENDPOINT_URL_LOGS": "http://localhost:4566",
            "AWS_ENDPOINT_URL_STS": "http://localhost:4566",
            "AWS_ENDPOINT_URL_CLOUDWATCH": "http://localhost:4566",
            "AWS_ENDPOINT_URL_ECR": "http://localhost:4566",
            "AWS_ENDPOINT_URL_KMS": "http://localhost:4566",
            # Configure CDK to use LocalStack S3 for assets
            "CDK_DEFAULT_REGION": self.region,
            "CDK_DEFAULT_ACCOUNT": "000000000000",
        }

    def bootstrap_cdk(self):
        """Run CDK bootstrap."""
        self.log("Running CDK bootstrap...")
        self.run_cmd(
            ["cdklocal", "bootstrap", "aws://000000000000/eu-west-1"],
            env=self.get_cdk_env(),
        )
        self.log_success("CDK bootstrap complete")

    def deploy_cdk(self) -> str:
        """Deploy CDK infrastructure, return baseline SFN ARN."""
        self.log("Deploying CDK infrastructure (OperationsBaselineStack + InferenceMonitorStack)...")

        result = self.run_cmd(
            ["cdklocal", "deploy", "--require-approval", "never", "--all"],
            env=self.get_cdk_env(),
        )

        # Extract baseline state machine ARN
        sfn_arn = None
        for line in result.stdout.split("\n"):
            if "BaselineStateMachineArn" in line:
                sfn_arn = line.split("=")[-1].strip()
                break

        if not sfn_arn:
            raise RuntimeError("Could not find BaselineStateMachineArn in deploy output")

        self.log_success(f"CDK deploy complete, baseline SFN ARN: {sfn_arn}")
        return sfn_arn

    def run_pytest_e2e_tests(self) -> bool:
        """Run pytest E2E tests with LOCALSTACK_TEST_ENABLED."""
        self.log("Running pytest E2E tests...")

        env = os.environ.copy()
        env["LOCALSTACK_TEST_ENABLED"] = "1"

        result = subprocess.run(
            [
                "uv",
                "run",
                "pytest",
                "tests/e2e/test_localstack_baseline.py",
                "tests/e2e/test_localstack_inference_monitor.py",
                "-v",
                "-m",
                "e2e",
            ],
            cwd=self.repo_root,
            env=env,
            capture_output=False,
        )

        return result.returncode == 0

    def run_tests(self):
        """Run all tests and return success status."""
        try:
            # Setup phase
            self.start_localstack()
            self.build_docker_images()
            self.set_env_vars()

            # Test phase (pytest will handle AWS resource setup, CDK bootstrap/deploy)
            tests_passed = self.run_pytest_e2e_tests()

            # Report
            print("\n" + "=" * 60)
            if tests_passed:
                self.log_success("All tests PASSED")
                print("=" * 60)
                return True
            else:
                self.log_error("Tests FAILED")
                print("=" * 60)
                return False

        except Exception as e:
            self.log_error(f"Test run failed: {e}")
            if self.verbose:
                import traceback

                traceback.print_exc()
            return False
        finally:
            self.stop_localstack()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Repeatable LocalStack E2E test runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Keep LocalStack running after tests (useful for debugging)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print verbose output",
    )

    args = parser.parse_args()

    # Find repo root
    repo_root = Path(__file__).parent.parent
    if not (repo_root / "docker-compose.localstack.yml").exists():
        print("Error: Could not find docker-compose.localstack.yml. Is this the repo root?")
        sys.exit(1)

    # Run tests
    runner = LocalStackTestRunner(
        repo_root=repo_root,
        verbose=args.verbose,
        cleanup=not args.no_cleanup,
    )

    success = runner.run_tests()
    sys.exit(0 if success else 4)


if __name__ == "__main__":
    main()
