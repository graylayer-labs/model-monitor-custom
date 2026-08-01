"""Container entrypoint — parses env, runs analyser, writes outputs.

Executed as ``python -m mmc_base.entrypoint``. Owns the whole IO contract;
analyser code is a pure ``compute(inputs, config) -> AnalyserOutput``.
"""

from __future__ import annotations

import importlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from aws_lambda_powertools import Logger

from mmc_base import ban_list, failure, io_cw, io_ddb, io_s3, provenance
from mmc_base.contract import AnalyserInputs, AnalyserOutput, EnvContract, Outcome, Severity

if TYPE_CHECKING:
    from mmc_base.analyser import Analyser

INPUT_DIR = Path("/tmp/mmc")  # ruff: ignore[hardcoded-temp-file] — container-scoped, not host tmp
ANALYSER_MODULE_ENV = "MMC_ANALYSER_MODULE"

logger = Logger(service="mmc-base")


def _import_analyser(spec: str) -> type[Analyser]:
    """Import ``pkg.mod:Class`` and return the class.

    Args:
        spec: ``"module.path:ClassName"``.

    Returns:
        The analyser class.

    Raises:
        ValueError: If ``spec`` is not ``mod:cls`` shaped.
    """
    if ":" not in spec:
        msg = f"MMC_ANALYSER_MODULE must be 'module:Class' — got {spec!r}"
        raise ValueError(msg)
    module_path, class_name = spec.split(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _bind_logger(env: EnvContract) -> None:
    """Attach the standard log context keys."""
    logger.append_keys(
        run_id=str(env.RUN_ID),
        analyser_type=env.ANALYSER_TYPE,
        project=env.PROJECT_NAME,
        environment=env.ENVIRONMENT,
        variant=env.VARIANT,
    )


def _validate_output(output: AnalyserOutput) -> None:
    """Raise if the analyser set the base-only ``failed_unhandled`` outcome.

    Args:
        output: Analyser return value.

    Raises:
        ValueError: If ``output.outcome`` is ``failed_unhandled``.
    """
    if output.outcome is Outcome.failed_unhandled:
        msg = "Analyser must not return Outcome.failed_unhandled — it is base-only."
        raise ValueError(msg)


def _run_analyser(env: EnvContract, input_dir: Path) -> AnalyserOutput:
    """Fetch config + inputs, import + call analyser, validate output.

    Args:
        env: Env contract.
        input_dir: Local directory to materialise inputs into.

    Returns:
        The analyser's structured output with ``run_ended_at`` set.
    """
    config = io_s3.fetch_config(env.CONFIG_URI)
    fetched = io_s3.fetch_inputs(env.input_uris, input_dir)
    inputs = AnalyserInputs(paths=fetched)
    analyser_cls = _import_analyser(os.environ[ANALYSER_MODULE_ENV])
    analyser = analyser_cls()
    output = analyser.compute(inputs, config)
    _validate_output(output)
    output.run_ended_at = datetime.now(UTC)
    return output


def _emit_success(output: AnalyserOutput, env: EnvContract) -> None:
    """Write S3 outputs, CW metrics, and the DDB outcome row.

    Args:
        output: Analyser output.
        env: Env contract.
    """
    prov = provenance.capture()
    io_s3.write_output(output, env.OUTPUT_URI, prov)
    io_cw.emit_standard_metrics(output, env)
    io_ddb.write_outcome_row(output, env, prov)


def _emit_failure(exc: BaseException, env: EnvContract, started_at: datetime) -> None:
    """Write ``failure.json``, DDB failure row, and log the exception.

    Args:
        exc: The exception caught at the top level.
        env: Env contract.
        started_at: Wall-clock start of the run.
    """
    prov = provenance.capture()
    _sidecar, failure_uri = failure.write_failure(exc, env, env.OUTPUT_URI, started_at, provenance=prov)
    ended = datetime.now(UTC)
    fake_output = AnalyserOutput(
        outcome=Outcome.failed_unhandled,
        severity=Severity.alert,
        run_started_at=started_at,
        run_ended_at=ended,
    )
    io_ddb.write_outcome_row(fake_output, env, prov, failure_uri=failure_uri)
    logger.error("analyser run failed", exc_info=exc)


def main() -> int:
    """Run the container.

    Returns:
        ``0`` on success, ``1`` on any unhandled failure.
    """
    ban_list.assert_clean_env()
    env = EnvContract.from_env()
    _bind_logger(env)
    started_at = datetime.now(UTC)
    logger.info("analyser run start")
    try:
        output = _run_analyser(env, INPUT_DIR)
        _emit_success(output, env)
    except Exception as exc:  # ruff: ignore[blind-except] — top-level guard
        _emit_failure(exc, env, started_at)
        return 1
    logger.info("analyser run complete", extra={"outcome": output.outcome.value})
    return 0


if __name__ == "__main__":
    sys.exit(main())
