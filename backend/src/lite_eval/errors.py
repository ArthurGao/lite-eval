"""Errors that decide the exit code (design §18.4)."""


class LiteEvalError(Exception):
    """Base class for lite-eval's own errors."""

    exit_code = 3


class ConfigError(LiteEvalError):
    """The suite, dataset or comparison is invalid, or the CI guard refused: exit 2."""

    exit_code = 2


class NotFound(ConfigError):
    """No such run, case or dataset: exit 2 on the command line, HTTP 404 in the Viewer."""


class RunError(LiteEvalError):
    """The runner itself failed, or no case was scored: exit 3."""

    exit_code = 3


class GateFailed(AssertionError):
    """A gate condition failed: exit 1. An AssertionError so pytest reports it as a failure."""

    exit_code = 1
