"""Comparing runs and gating them (design §8.2, §8.4, §11.3, §18.4)."""

from collections.abc import Sequence
from pathlib import Path

from lite_eval.analysis.compare import compare
from lite_eval.analysis.gate import GateOutcome, evaluate_gate
from lite_eval.contracts import Comparison
from lite_eval.errors import ConfigError, GateFailed, RunError
from lite_eval.report.terminal import render_gate
from lite_eval.runner import RunResult
from lite_eval.store import StoredRun, baseline_run, load_run, read_record, root_of


def compare_runs(baseline: str | Path, candidate: str | Path) -> Comparison:
    """Pair two run directories: baseline first."""
    a = load_run(Path(baseline), outputs=False)
    b = load_run(Path(candidate), outputs=False)
    return compare(a.record, a.scores, b.record, b.scores)


def gate_results(results: Sequence[RunResult]) -> list[GateOutcome]:
    """One outcome per run, from what the runner holds; each baseline is read once.
    Raises ConfigError for a gate that cannot be evaluated, or that judges none of the runs
    (exit 2); a run no rule applies to next to a judged one is reported, not failed."""
    baselines: dict[tuple[Path, str], StoredRun | None] = {}
    outcomes = []
    for r in results:
        rec = r.record
        base = None
        if rec.gate is not None and rec.gate.relative:
            key = (root_of(r.directory), rec.suite.name)
            if key not in baselines:
                baselines[key] = baseline_run(*key)
            base = baselines[key]
        comparison = compare(base.record, base.scores, rec, r.scores) if base else None
        outcomes.append(evaluate_gate(rec, base.record if base else None, comparison))
    if outcomes and not any(o.applied or o.failures for o in outcomes):
        # Rules scoped to configs that did not run judge nothing: not a pass (Codex E4d).
        configs = sorted(o.config for o in outcomes)
        raise ConfigError(f"no gate rule applies to any of these runs (configs {configs})")
    return outcomes


def assert_gate(results: Sequence[RunResult]) -> None:
    """For pytest: RunError if a run scored nothing, ConfigError for a bad gate, GateFailed
    (an AssertionError) naming each failed condition and the regressed cases."""
    failed = [r.record.run_id for r in results if r.record.status == "failed"]
    if failed:
        raise RunError(f"no case was scored in run(s) {failed}")
    outcomes = gate_results(results)
    if not all(o.passed for o in outcomes):
        raise GateFailed("\n".join(render_gate(o) for o in outcomes if not o.passed))


def export_kb(run_dirs: Sequence[str | Path]) -> str:
    """Rows for the KB's llm-evals `results.jsonl` (design §13, §18.24), from runs of one suite;
    the suite's `kb:` section gives what a record cannot know. Checked against the KB schema."""
    from lite_eval.report.kb import kb_rows, render_rows
    from lite_eval.suite import load_suite

    if not run_dirs:
        raise ConfigError("export kb needs at least one run")
    records = [read_record(Path(d)) for d in run_dirs]  # run.json only: no outputs, no scores
    suites = {r.suite.name for r in records}
    if len(suites) != 1:
        raise ConfigError(f"export kb takes runs of one suite, got {sorted(suites)}")
    first = records[0]
    if first.suite.path is None:
        raise ConfigError(f"run {first.run_id} does not record its suite path")
    root = root_of(Path(run_dirs[0]))
    suite = load_suite(root / first.suite.path, root)
    if suite.spec.kb is None:
        raise ConfigError(f"{suite.path}: no `kb:` section to export with (design §18.24)")
    return render_rows(kb_rows(records, suite.spec.kb))
