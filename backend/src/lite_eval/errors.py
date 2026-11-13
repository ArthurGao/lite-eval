"""Errors that decide the exit code (design §18.4)."""


class LiteEvalError(Exception):
    """Base class for lite-eval's own errors."""

    exit_code = 3


class ConfigError(LiteEvalError):
    """The suite, dataset or comparison is invalid, or the CI guard refused: exit 2."""

    exit_code = 2


class RunError(LiteEvalError):
    """The runner itself failed, or no case was scored: exit 3."""

    exit_code = 3
