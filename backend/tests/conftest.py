import os

# Typer reads TERMINAL_WIDTH when typer.rich_utils is imported (typer 0.29.1, rich_utils.py),
# so it must be set before any test module imports lite_eval.cli. A wide terminal keeps rich
# from wrapping help text in assertions.
os.environ["TERMINAL_WIDTH"] = "200"
