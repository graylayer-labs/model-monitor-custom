#!/usr/bin/env python3
"""AWS E2E test orchestrator.

Handles: deploy → test → cleanup lifecycle for AWS E2E tests.

Usage:
    python3 scripts/aws-e2e-test.py --cleanup --verbose

Environment variables:
    AWS_REGION: AWS region (default: eu-west-1)
    AWS_ACCOUNT_ID: AWS account ID (required)
    AWS_ACCESS_KEY_ID: AWS credentials (required)
    AWS_SECRET_ACCESS_KEY: AWS credentials (required)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


class AWSTestOrchestrator:
    """Orchestrates AWS E2E test lifecycle."""

    def __init__(self, region: str = "eu-west-1", verbose: bool = False, cleanup: bool = True):
        """Initialize orchestrator.

        Args:
            region: AWS region
            verbose: Print verbose output
            cleanup: Clean up resources after tests
        """
        self.region = region
        self.verbose = verbose
        self.cleanup = cleanup
        self.repo_root = Path(__file__).parent.parent
        self.project = "mmc-aws-test"
        self.stacks_deployed = []

    def log(self, msg: str, level: str = "INFO"):
        """Print log message."""
        prefix = f"[{level}]" if level != "INFO" else "[•]"
        print(f"{prefix} {msg}")

    def log_verbose(self, msg: str):
        """Print verbose log."""
        if self.verbose:
            self.log(msg, "DEBUG")

    def log_error(self, msg: str):
        """Print error message."""
        self.log(msg, "ERROR")

    def run_cmd(
        self, cmd: list[str], check: bool = True, capture: bool = False
    ) -> subprocess.CompletedProcess:
        """Run shell command.

        Args:
            cmd: Command list
            check: Raise on non-zero exit
            capture: Capture output (don't print)

        Returns:
            CompletedProcess
        """
        self.log_verbose(f"Running: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            cwd=self.repo_root,
            capture_output=capture,
            text=True,
            check=False,
        )

        if check and result.returncode != 0:
            self.log_error(f"Command failed with exit code {result.returncode}")
            if result.stdout:
                print(result.stdout[-1000:])
            if result.stderr:
                print(result.stderr[-1000:])
            raise subprocess.CalledProcessError(result.returncode, result.args)

        return result

    def deploy_cdk(self):
        """Deploy CDK stacks to AWS."""
        self.log("Step 1: Deploying CDK stacks...")

        env = os.environ.copy()
        env["CDK_DEFAULT_REGION"] = self.region
        env["CDK_DEFAULT_ACCOUNT"] = os.environ["AWS_ACCOUNT_ID"]

        # Deploy all stacks for the test project
        result = subprocess.run(
            ["uv", "run", "cdk", "deploy", "*", "--require-approval", "never"],
            cwd=self.repo_root / "cdk",
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            self.log_error("CDK deploy failed")
            if result.stderr:
                print(result.stderr[-2000:])
            raise RuntimeError("CDK deployment failed")

        self.log("CDK stacks deployed successfully")

        # Extract stack names from output
        if "MMC-" in result.stdout:
            for line in result.stdout.split("\n"):
                if "MMC-" in line:
                    self.log_verbose(line)

    def run_pytest(self):
        """Run AWS E2E tests."""
        self.log("Step 2: Running AWS E2E tests...")

        env = os.environ.copy()
        env["AWS_REGION"] = self.region
        # AWS credentials already in env

        result = subprocess.run(
            [
                "uv",
                "run",
                "pytest",
                "tests/aws/test_e2e.py",
                "-v",
                "-m",
                "aws",
                "--tb=short",
            ],
            cwd=self.repo_root,
            env=env,
            capture_output=False,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            self.log_error("Tests failed")
            return False

        self.log("Tests passed")
        return True

    def cleanup_cdk(self):
        """Clean up CDK stacks."""
        if not self.cleanup:
            self.log("Skipping cleanup (--no-cleanup specified)")
            return

        self.log("Step 3: Cleaning up CDK stacks...")

        env = os.environ.copy()
        env["CDK_DEFAULT_REGION"] = self.region
        env["CDK_DEFAULT_ACCOUNT"] = os.environ["AWS_ACCOUNT_ID"]

        # Destroy all stacks
        result = subprocess.run(
            ["uv", "run", "cdk", "destroy", "*", "--force"],
            cwd=self.repo_root / "cdk",
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            self.log_error("CDK cleanup had issues (may be expected)")
            if self.verbose and result.stderr:
                print(result.stderr[-1000:])
        else:
            self.log("Cleanup completed")

    def run(self) -> bool:
        """Run full test lifecycle.

        Returns:
            True if tests passed, False otherwise
        """
        try:
            self.deploy_cdk()
            tests_passed = self.run_pytest()
            return tests_passed

        except Exception as e:
            self.log_error(f"Test run failed: {e}")
            if self.verbose:
                import traceback
                traceback.print_exc()
            return False

        finally:
            self.cleanup_cdk()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="AWS E2E test orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--region",
        default="eu-west-1",
        help="AWS region (default: eu-west-1)",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Keep AWS resources after tests (for debugging)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print verbose output",
    )

    args = parser.parse_args()

    # Verify required env vars
    for var in ["AWS_ACCOUNT_ID", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]:
        if not os.environ.get(var):
            print(f"Error: {var} environment variable required")
            sys.exit(1)

    # Run orchestrator
    orchestrator = AWSTestOrchestrator(
        region=args.region,
        verbose=args.verbose,
        cleanup=not args.no_cleanup,
    )

    print("\n" + "=" * 60)
    print("AWS E2E Test Orchestrator")
    print("=" * 60)
    print(f"Region: {args.region}")
    print(f"Account: {os.environ.get('AWS_ACCOUNT_ID')}")
    print(f"Cleanup: {not args.no_cleanup}")
    print("=" * 60 + "\n")

    success = orchestrator.run()

    print("\n" + "=" * 60)
    if success:
        print("✓ AWS E2E tests PASSED")
        sys.exit(0)
    else:
        print("✗ AWS E2E tests FAILED")
        sys.exit(4)


if __name__ == "__main__":
    main()
