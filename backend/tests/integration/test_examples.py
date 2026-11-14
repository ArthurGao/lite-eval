"""The shipped examples run as documented."""

import os
import sys
from pathlib import Path

import pytest

from lite_eval.runner import run_suite
from lite_eval.targets.command import STDERR_LOG

EXAMPLES = Path(__file__).parents[2] / "examples"


def test_the_subprocess_example_runs_and_announces_its_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The example runs `python` from PATH, as under `uv run`; CI's lowest job calls the venv's
    # interpreter directly, without the venv on PATH.
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    results = run_suite(EXAMPLES / "suites" / "command-demo.yaml", root=tmp_path, ci=False)
    by_config = {str(r.record.target.config["name"]): r for r in results}
    # "japan" is lower case: only the fuzzy config title-cases it
    assert by_config["strict"].record.summary.metrics["exact_match"].value == pytest.approx(2 / 3)
    assert by_config["fuzzy"].record.summary.metrics["exact_match"].value == 1.0
    assert "capitals table loaded" in (by_config["fuzzy"].directory / STDERR_LOG).read_text()
    announced = capsys.readouterr().err
    assert "lite-eval: config fuzzy: python command_target.py (cwd " in announced
