import os

import pytest

# Typer reads these when typer.rich_utils is imported (typer 0.29.1, rich_utils.py lines
# 71-83), so they must be set before any test module imports lite_eval.cli:
# - TERMINAL_WIDTH: a wide terminal keeps rich from wrapping help text in assertions;
# - _TYPER_FORCE_DISABLE_TERMINAL: typer forces a colour terminal when GITHUB_ACTIONS,
#   FORCE_COLOR or PY_COLORS is set, and the ANSI codes split "Usage: lite-eval" in CI
#   (first E0 CI run, 2026-11-13).
os.environ["TERMINAL_WIDTH"] = "200"
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"

# Tests allowed to skip in CI. Empty: in CI a skip is a failure (KB testing-and-ci §1: a green
# run once hid the only two tests that exercised the feature).
CI_SKIP_ALLOWED: frozenset[str] = frozenset()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not os.environ.get("CI"):
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = [r.nodeid for r in getattr(reporter, "stats", {}).get("skipped", [])]
    unexpected = [n for n in skipped if n not in CI_SKIP_ALLOWED]
    if unexpected:
        print(f"\nCI refuses skipped tests: {unexpected}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
