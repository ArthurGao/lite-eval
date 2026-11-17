"""The `lite-eval` command line. Thin: parses arguments and calls the public API (design §11.2).

Exit codes (design §18.4): 0 pass, 1 gate failed (from E2), 2 config or input error, 3 run failed.
"""

from pathlib import Path
from types import ModuleType
from typing import Annotated, NoReturn

import typer

from lite_eval import __version__
from lite_eval.api import compare_runs, export_kb, gate_results
from lite_eval.contracts import AnnotationCategory
from lite_eval.errors import ConfigError, LiteEvalError
from lite_eval.report.terminal import render_comparison, render_gate, render_run
from lite_eval.runner import RunResult, rescore_run, run_suite
from lite_eval.store import BASELINE_LOG, load_run, root_of, set_baseline

app = typer.Typer(name="lite-eval", no_args_is_help=True, add_completion=False)
baseline_app = typer.Typer(
    no_args_is_help=True, help="Set the run a suite compares and gates against."
)
app.add_typer(baseline_app, name="baseline")
dataset_app = typer.Typer(no_args_is_help=True, help="Import external benchmarks as datasets.")
app.add_typer(dataset_app, name="dataset")
export_app = typer.Typer(no_args_is_help=True, help="Export run results to other places.")
app.add_typer(export_app, name="export")


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


def _finish(results: list[RunResult], gate: bool = False) -> None:
    """Exit codes in the order of design §18.4: 3 run failed, then 1 gate failed, else 0."""
    for r in results:
        typer.echo(render_run(r.record, str(r.directory), r.scores))
    if any(r.record.status == "failed" for r in results):
        typer.echo("run failed: no case was scored", err=True)
        raise typer.Exit(3)
    if gate:
        try:
            outcomes = gate_results(results)
        except LiteEvalError as exc:
            _fail(exc)
        for o in outcomes:
            typer.echo(render_gate(o))
        if not all(o.passed for o in outcomes):
            raise typer.Exit(1)


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
    gate: Annotated[
        bool, typer.Option("--gate", help="Evaluate the suite's gate; exit 1 when it fails.")
    ] = False,
    crosscheck: Annotated[
        bool,
        typer.Option("--crosscheck", help="Recompute ranking metrics with ranx; warn on a gap."),
    ] = False,
) -> None:
    """Generate outputs, score them and write one run directory per config."""
    try:
        results = run_suite(
            suite,
            config=config,
            no_cache=no_cache,
            ci=ci,
            root=root,
            gate=gate,
            crosscheck=crosscheck,
        )
    except LiteEvalError as exc:
        _fail(exc)
    _finish(results, gate=gate)


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


@app.command()
def compare(
    baseline: Annotated[Path, typer.Argument(help="Baseline run directory.")],
    candidate: Annotated[Path, typer.Argument(help="Candidate run directory.")],
) -> None:
    """Pair two runs case by case: deltas, significance and the cases that changed."""
    try:
        comparison = compare_runs(baseline, candidate)
    except LiteEvalError as exc:
        _fail(exc)
    typer.echo(render_comparison(comparison))


@baseline_app.command("set")
def baseline_set(
    run_dir: Annotated[Path, typer.Argument(help="A finished run directory.")],
) -> None:
    """Make this run its suite's baseline (written to <root>/baselines.yaml)."""
    try:
        stored = load_run(run_dir)
        path = set_baseline(stored)
    except LiteEvalError as exc:
        _fail(exc)
    typer.echo(f"baseline of {stored.record.suite.name}: {stored.record.run_id} ({path})")
    typer.echo(
        f"commit {run_dir} and {path.name} (with {BASELINE_LOG}) with the code they gate "
        "(design §17)"
    )


@dataset_app.command("import")
def dataset_import(
    loader: Annotated[str, typer.Argument(help="longmemeval or locomo.")],
    src: Annotated[Path, typer.Argument(help="The official benchmark file (JSON).")],
    out: Annotated[Path, typer.Argument(help="A new dataset directory to write.")],
    limit: Annotated[
        int | None, typer.Option(help="Only the first N questions of the file.")
    ] = None,
    include_abstention: Annotated[
        bool, typer.Option(help="Keep LongMemEval's abstention (_abs) questions.")
    ] = False,
    name: Annotated[str | None, typer.Option(help="Dataset name (default: the loader).")] = None,
) -> None:
    """Write an external benchmark file as a dataset directory (design §18.23)."""
    from lite_eval.datasets.loaders import import_dataset
    from lite_eval.datasets.local import load_dataset

    try:
        directory = import_dataset(
            loader, src, out, name=name, limit=limit, include_abstention=include_abstention
        )
        dataset = load_dataset(directory)
    except LiteEvalError as exc:
        _fail(exc)
    meta = dataset.meta
    typer.echo(
        f"{meta.name} {meta.version}: {len(dataset.cases)} cases -> {directory} "
        f"(license {meta.license}; {meta.description})"
    )


def _server_module() -> ModuleType:
    try:
        import lite_eval.server.run as server_run
    except ImportError as exc:  # fastapi / uvicorn come with the `server` extra
        _fail(
            ConfigError(
                f"the Viewer needs the server extra: pip install 'lite-eval[server]' ({exc})"
            )
        )
    return server_run


_STATIC_HELP = "Built frontend to serve (default: the one packaged with lite-eval)."


@app.command()
def view(
    root: Annotated[
        Path | None, typer.Option(help="Evaluation root (default: ., else ./evals).")
    ] = None,
    host: Annotated[str, typer.Option(help="Loopback address to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8720,
    check: Annotated[
        bool, typer.Option("--check", help="Start on a free port, request / and /api/health, exit.")
    ] = False,
    static: Annotated[Path | None, typer.Option(hidden=True, help=_STATIC_HELP)] = None,
) -> None:
    """The Viewer: runs, comparisons, cases, miss triage, datasets and trends (loopback only)."""
    from lite_eval.server.paths import NO_FRONTEND, STATIC

    server_run = _server_module()
    static_dir = static or STATIC
    try:
        if check:
            codes = server_run.check(root, static_dir)
            typer.echo(f"view check: / {codes['/']}, /api/health {codes['/api/health']}")
            if set(codes.values()) != {200}:
                if codes["/"] == 503:
                    typer.echo(NO_FRONTEND, err=True)
                raise typer.Exit(3)
            return
        server_run.check_host(host)
        resolved = server_run.resolve_root(root, Path.cwd())
        if not (static_dir / "index.html").is_file():
            typer.echo(f"warning: {NO_FRONTEND}", err=True)
        typer.echo(f"lite-eval Viewer on http://{host}:{port}  (root {resolved}; Ctrl-C stops)")
        server_run.serve(resolved, host, port, static_dir)
    except LiteEvalError as exc:
        _fail(exc)


_CATEGORIES: dict[str, AnnotationCategory] = {
    "r": "real_miss",
    "l": "label_wrong",
    "e": "excluded",
    "u": "unclear",
}


def _short(value: object, limit: int = 400) -> str:
    import json

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"… ({len(text)} chars)"


@app.command()
def triage(
    run_dir: Annotated[Path, typer.Argument(help="A run directory (runs/<suite>/<run_id>).")],
) -> None:
    """Classify this run's untriaged misses one by one (design §18.9); the Viewer does the same."""
    from lite_eval.annotations import annotate
    from lite_eval.views import untriaged

    try:
        record = load_run(run_dir, outputs=False).record
        root = root_of(run_dir)
        todo = untriaged(root, record.suite.name, record.run_id)
    except LiteEvalError as exc:
        _fail(exc)
    if not todo:
        typer.echo("nothing to triage: every miss of this run has a current annotation")
        return
    done = skipped = 0
    for n, (row, case) in enumerate(todo, start=1):
        typer.echo(
            f"\n[{n}/{len(todo)}] {row.case_id}  {row.tags}  scores {row.scores}"
            + (f"  error {row.error}" if row.error else "")
        )
        if case.content.case is not None:
            if case.content.status == "changed":
                typer.echo("  (the dataset changed since this run; showing today's content)")
            typer.echo(f"  input:    {_short(case.content.case.input)}")
            typer.echo(f"  expected: {_short(case.content.case.expected)}")
        for o in case.outputs:
            typer.echo(f"  output:   {_short(o.error.message if o.error else o.value)}")
        if row.older_annotation:
            typer.echo("  (an older version of this case was triaged before)")
        choice = ""
        while choice not in (*_CATEGORIES, "s", "q"):
            # typer 0.29 vendors click, so a click.Choice is not a type its prompt accepts
            choice = (
                typer.prompt("  [r]eal miss, [l]abel wrong, [e]xcluded, [u]nclear, [s]kip, [q]uit")
                .strip()
                .lower()
            )
        if choice == "q":
            break
        if choice == "s":
            skipped += 1
            continue
        note = typer.prompt("  note (optional)", default="", show_default=False)
        try:
            annotate(
                root,
                dataset=record.dataset.name,
                case_id=row.case_id,
                case_hash=case.case_hash,
                run_id=record.run_id,
                category=_CATEGORIES[choice],
                note=note,
            )
        except LiteEvalError as exc:  # someone triaged it meanwhile, or a note over 4 KB
            typer.echo(f"  not written: {exc}", err=True)
            continue
        done += 1
    typer.echo(f"\n{done} triaged, {skipped} skipped")


def _run_dirs(target: str) -> list[Path]:
    """One run directory, or two joined by `..` (split where both sides are directories, so
    relative paths such as ../runs/x keep working)."""
    if Path(target).is_dir():
        return [Path(target)]
    for i in range(len(target)):
        if target.startswith("..", i):
            a, b = Path(target[:i]), Path(target[i + 2 :])
            if target[:i] and target[i + 2 :] and a.is_dir() and b.is_dir():
                return [a, b]
    raise ConfigError(f"not a run directory or <baseline dir>..<candidate dir>: {target!r}")


@app.command()
def report(
    target: Annotated[
        str, typer.Argument(help="A run directory, or <baseline dir>..<candidate dir>.")
    ],
    html: Annotated[bool, typer.Option("--html", help="Write one self-contained HTML file.")],
    out: Annotated[Path, typer.Option("-o", "--out", help="Output file.")],
    redact: Annotated[
        list[str] | None, typer.Option(help="Built-in rule to add; repeat it. Never removes one.")
    ] = None,
    static: Annotated[Path | None, typer.Option(hidden=True, help=_STATIC_HELP)] = None,
) -> None:
    """Export a run, or a comparison of two, as a page that opens with no server (design §10.2)."""
    from lite_eval.report.html import render
    from lite_eval.server.paths import STATIC

    if not html:
        _fail(ConfigError("only --html is supported"))
    try:
        page = render(static or STATIC, _run_dirs(target), redact or [])
    except LiteEvalError as exc:
        _fail(exc)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    typer.echo(f"wrote {out} ({len(page.encode()) // 1024} KB)")


@export_app.command("kb")
def export_kb_command(
    runs: Annotated[list[Path], typer.Argument(help="Run directories of one suite.")],
) -> None:
    """Rows for the KB's llm-evals results.jsonl, one per run and metric (stdout); the suite's
    `kb:` section names the eval card, task, dataset, grader and the metrics to export."""
    try:
        typer.echo(export_kb(runs), nl=False)
    except LiteEvalError as exc:
        _fail(exc)
