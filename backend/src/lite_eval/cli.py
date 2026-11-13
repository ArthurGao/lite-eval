"""The `lite-eval` command line.

Commands are added in the stage that implements them (design §11.2).
"""

from typing import Annotated

import typer

from lite_eval import __version__

app = typer.Typer(
    name="lite-eval",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"lite-eval {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Print the version and exit.",
            callback=_print_version,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """lite-eval: did this change make the system better or worse, and can we trust that?"""
