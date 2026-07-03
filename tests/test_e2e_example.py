"""End-to-end integration test for the Adult example.

Marked ``@pytest.mark.e2e`` — opt-in, slow relative to unit tests because
it trains a model and drives every analyser. Verifies:

- All five analysers produce outputs (with the DQ control adding a sixth
  run for the no-drift check).
- Each analyser lands on the expected outcome for the deterministic
  fixture — drift shows up where it should, and the clean control window
  stays clean.
- The auto-generated docs snapshot + JSON summary are on disk after the
  run.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.e2e
def test_e2e_example_runs_all_analysers():
    from mmc_example_adult.run import DOC_PATH, SUMMARY_PATH, main

    summary = main()

    runs = {entry["analyser"]: entry for entry in summary["runs"]}
    assert set(runs) == {"bias", "explain", "dq", "dq_control", "mq", "shadow"}

    assert runs["bias"]["outcome"] == "succeeded_with_violations"
    assert runs["explain"]["outcome"] == "succeeded"
    assert runs["dq"]["outcome"] == "succeeded_with_violations"
    assert runs["dq_control"]["outcome"] == "succeeded"
    assert runs["dq_control"]["violation_count"] == 0
    assert runs["mq"]["outcome"] == "succeeded_with_violations"
    assert runs["shadow"]["outcome"] == "succeeded_with_violations"

    assert Path(SUMMARY_PATH).exists()
    assert Path(DOC_PATH).exists()
    for entry in summary["runs"]:
        if entry["plot"] is not None:
            assert Path(entry["plot"]).exists() or (Path.cwd() / entry["plot"]).exists()
