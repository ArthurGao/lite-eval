"""Comparing runs and gating them (design §8.2, §8.4, §11.3, §18.4)."""

from collections.abc import Sequence
from pathlib import Path

from lite_eval.analysis.compare import compare
from lite_eval.analysis.gate import GateOutcome, evaluate_gate
from lite_eval.contracts import Comparison
from lite_eval.errors import GateFailed, RunError
from lite_eval.report.terminal import render_gate
from lite_eval.runner import RunResult
from lite_eval.store import StoredRun, baseline_run, load_run, root_of


def compare_runs(baseline: str | Path, candidate: str | Path) -> Comparison:
    """Pair two run directories: baseline first."""
    a = load_run(Path(baseline), outputs=False)
    b = load_run(Path(candidate), outputs=False)
    return compare(a.record, a.scores, b.record, b.scores)


def gate_results(results: Sequence[RunResult]) -> list[GateOutcome]:
    """One outcome per run, from what the runner holds; each baseline is read once.
    Raises ConfigError for a gate that cannot be evaluated (exit 2)."""
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
