"""lite-eval: a general evaluation module for AI applications (design: docs/lite-eval-design.md)."""

from importlib.metadata import version

__version__ = version("lite-eval")

# Public API (design §11.3). Imported after __version__, which the runner records.
from lite_eval.api import assert_gate, compare_runs
from lite_eval.errors import ConfigError, GateFailed, RunError
from lite_eval.runner import rescore_run, run_suite

__all__ = [
    "ConfigError",
    "GateFailed",
    "RunError",
    "__version__",
    "assert_gate",
    "compare_runs",
    "rescore_run",
    "run_suite",
]
