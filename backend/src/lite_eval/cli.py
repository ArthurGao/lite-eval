"""The `lite-eval` command line. Thin: parses arguments and calls the public API (design §11.2).

Exit codes (design §18.4): 0 pass, 1 gate failed (from E2), 2 config or input error, 3 run failed.
"""

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from lite_eval import __version__
from lite_eval.errors import LiteEvalError
from lite_eval.report.terminal import render_run
from lite_eval.runner import RunResult, rescore_run, run_suite

app = typer.Typer(name="lite-eval", no_args_is_help=True, add_completion=False)


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


def _finish(results: list[RunResult]) -> None:
    for r in results:
        typer.echo(render_run(r.record, str(r.directory)))
    if any(r.record.status == "failed" for r in results):
        typer.echo("run failed: no case was scored", err=True)
        raise typer.Exit(3)


def _fail(exc: LiteEvalError) -> NoReturn:
    typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(exc.exit_code)


@app.command()
def run(
    suite: Annotated[Path, typer.Argument(help="Suite YAML file.")],
    config: Annotated[str | None, typer.Option(help="Run only this config.")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Regenerate every output.")] = False,
    ci: Annotated[
        bool | None,
        typer.Option(
            "--ci/--no-ci", help="CI mode (default: on when CI=true); refuses paid calls."
        ),
    ] = None,
    root: Annotated[
        Path | None, typer.Option(help="Evaluation root (default from the suite).")
    ] = None,
) -> None:
    """Generate outputs, score them and write one run directory per config."""
    try:
        results = run_suite(suite, config=config, no_cache=no_cache, ci=ci, root=root)
    except LiteEvalError as exc:
        _fail(exc)
    _finish(results)


@app.command()
def rescore(
    run_dir: Annotated[Path, typer.Argument(help="A run directory (runs/<suite>/<run_id>).")],
    scorer: Annotated[
        list[str] | None, typer.Option(help="Scorer name; repeat it. Default: the run's scorers.")
    ] = None,
) -> None:
    """Score a run's stored outputs again without calling the target; writes a new run."""
    try:
        result = rescore_run(run_dir, scorer)
    except LiteEvalError as exc:
        _fail(exc)
    _finish([result])
