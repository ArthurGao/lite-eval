import os

# Typer reads these when typer.rich_utils is imported (typer 0.29.1, rich_utils.py lines
# 71-83), so they must be set before any test module imports lite_eval.cli:
# - TERMINAL_WIDTH: a wide terminal keeps rich from wrapping help text in assertions;
# - _TYPER_FORCE_DISABLE_TERMINAL: typer forces a colour terminal when GITHUB_ACTIONS,
#   FORCE_COLOR or PY_COLORS is set, and the ANSI codes split "Usage: lite-eval" in CI
#   (first E0 CI run, 2026-11-13).
os.environ["TERMINAL_WIDTH"] = "200"
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"
