"""Gates, baselines and exit codes through the CLI (design §8.4, §18.3, §18.4, §18.19)."""

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from lite_eval import assert_gate, run_suite
from lite_eval.cli import app
from lite_eval.errors import ConfigError, GateFailed, RunError
from tests.helpers import write_suite

runner = CliRunner()

CASES = [{"id": f"q{i:02d}", "input": i, "expected": f"a{i}"} for i in range(20)]

MODULE = """
from lite_eval.contracts import Output, Trace, Usage

CALLS = []

class Answers:
    name = "answers"
    def __init__(self, wrong=(), latency_ms=10.0):
        self.wrong, self.latency_ms = list(wrong), latency_ms
    def config(self): return {"wrong": self.wrong, "latency_ms": self.latency_ms}
    def versions(self): return {"answers": "1"}
    def info(self): return {}
    def billing(self): return "free"
    def setup_identity(self): return {}
    async def setup(self, dataset): pass
    async def teardown(self): pass
    async def run(self, case):
        CALLS.append(case.id)
        value = "no" if case.id in self.wrong else case.expected
        return Output(case_id=case.id, value=value, trace=Trace(latency_ms=self.latency_ms))
"""


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Path]:
    (tmp_path / "answers_target.py").write_text(MODULE)
    yield tmp_path
    sys.modules.pop("answers_target", None)


def suite(repo: Path, gate: Any, wrong: list[str] | None = None, **spec: Any) -> Path:
    config: dict[str, Any] = {"name": "cand", "wrong": wrong or []}
    return write_suite(
        repo / "evals",
        cases=spec.pop("cases", CASES),
        targets=[{"python": "answers_target:Answers", "configs": [config]}],
        gate=gate,
        **spec,
    )


def calls() -> list[str]:
    return list(sys.modules["answers_target"].CALLS) if "answers_target" in sys.modules else []


def run_dirs(repo: Path) -> list[Path]:
    base = repo / "evals" / "runs" / "demo-suite"
    return sorted(base.iterdir()) if base.exists() else []


def set_baseline(repo: Path, wrong: list[str]) -> Path:
    """Run a baseline config and make it the suite's baseline; returns its directory."""
    s = suite(repo, [{"metric": "exact_match", "min": 0}], wrong)
    assert runner.invoke(app, ["run", str(s)]).exit_code == 0
    (directory,) = run_dirs(repo)
    assert runner.invoke(app, ["baseline", "set", str(directory)]).exit_code == 0
    return directory


def gate_run(repo: Path, gate: Any, wrong: list[str] | None = None, **spec: Any) -> Any:
    return runner.invoke(app, ["run", str(suite(repo, gate, wrong, **spec)), "--gate"])


# ---------------------------------------------------------------- absolute rules


def test_an_absolute_minimum_that_holds_exits_zero(repo: Path) -> None:
    result = gate_run(
        repo, [{"metric": "exact_match", "min": 0.9, "baseline_note": "0.95 on 2026-11-14"}]
    )

    assert result.exit_code == 0, result.output
    assert "gate passed: cand" in result.output


def test_an_absolute_minimum_that_fails_exits_one(repo: Path) -> None:
    result = gate_run(repo, [{"metric": "exact_match", "min": 0.9}], wrong=["q01", "q02", "q03"])

    assert result.exit_code == 1
    assert "exact_match = 0.8500 < min 0.9" in result.output


@pytest.mark.parametrize(("latency_ms", "exit_code"), [(10.0, 0), (80.0, 1)])
def test_a_latency_maximum_reads_the_cost_summary(
    repo: Path, latency_ms: float, exit_code: int
) -> None:
    s = write_suite(
        repo / "evals",
        cases=CASES,
        targets=[
            {
                "python": "answers_target:Answers",
                "configs": [{"name": "cand", "latency_ms": latency_ms}],
            }
        ],
        gate=[{"metric": "latency_p95", "max": 50}],
    )
    result = runner.invoke(app, ["run", str(s), "--gate"])

    assert result.exit_code == exit_code, result.output
    assert (f"latency_p95 = {latency_ms:.4f} > max 50" in result.output) == (exit_code == 1)


# ------------------------------------------- configuration errors: exit 2, before any call


@pytest.mark.parametrize(
    ("gate", "message"),
    [
        (None, "the suite declares no `gate`"),
        ([{"metric": "hit@5", "min": 0.9}], "gate metric 'hit@5' is not scored"),
        (
            [{"metric": "exact_match", "compare_to": "baseline", "max_drop": 0.02}],
            "needs a baseline",
        ),
        ([{"metric": "exact_match", "min": 0.9, "max_drop": 0.1}], "needs either min/max"),
        (
            [{"metric": "latency_p50", "compare_to": "baseline", "max_drop": 5}],
            "latency_p50 cannot be compared to the baseline",
        ),
    ],
)
def test_gate_configuration_errors_exit_two_before_the_target_runs(
    repo: Path, gate: Any, message: str
) -> None:
    result = gate_run(repo, gate)

    assert result.exit_code == 2
    assert message in result.output
    assert calls() == [] and run_dirs(repo) == []


def test_a_failed_run_exits_three_before_the_gate_is_judged(repo: Path) -> None:
    result = gate_run(repo, [{"metric": "exact_match", "min": 0.9}], budget={"max_calls": 0})

    assert result.exit_code == 3


def test_a_partial_run_fails_the_gate_unless_allowed(repo: Path) -> None:
    budget = {"max_calls": 5}
    partial = gate_run(repo, [{"metric": "exact_match", "min": 0}], budget=budget, concurrency=1)
    allowed = gate_run(
        repo,
        {"rules": [{"metric": "exact_match", "min": 0}], "allow_partial": True},
        budget=budget,
        concurrency=1,
        cache="off",  # otherwise the second run is complete from the first one's cache
    )

    assert partial.exit_code == 1 and "the run is partial" in partial.output
    assert allowed.exit_code == 0, allowed.output


# ------------------------------------------- relative rules against the baseline


RELATIVE = [{"metric": "exact_match", "compare_to": "baseline", "max_drop": 0.05}]


def test_a_drop_beyond_max_drop_fails_and_lists_the_regressed_cases(repo: Path) -> None:
    set_baseline(repo, [])
    result = gate_run(repo, RELATIVE, wrong=["q03", "q11"])

    assert result.exit_code == 1
    assert "exact_match dropped 0.1000 from the baseline" in result.output
    assert "regressed cases (2): q03, q11" in result.output


def test_require_significant_lets_a_small_noisy_drop_pass(repo: Path) -> None:
    set_baseline(repo, [])
    gate = [{**RELATIVE[0], "require_significant": True}]
    result = gate_run(repo, gate, wrong=["q03", "q11"])  # McNemar b=2, c=0: p = 0.5

    assert result.exit_code == 0, result.output


def test_a_drop_within_max_drop_passes(repo: Path) -> None:
    set_baseline(repo, [])

    assert gate_run(repo, RELATIVE, wrong=["q03"]).exit_code == 0


def test_a_changed_dataset_refuses_a_relative_gate_unless_partial_overlap_is_allowed(
    repo: Path,
) -> None:
    set_baseline(repo, [])
    cases = [dict(c) for c in CASES]
    cases[0]["expected"] = "changed"

    runs_before = len(run_dirs(repo))
    refused = gate_run(repo, RELATIVE, cases=cases)
    assert len(run_dirs(repo)) == runs_before  # refused before any target call (§18.4)
    allowed = gate_run(
        repo, {"rules": RELATIVE, "allow_partial_overlap": True}, cases=cases, wrong=["q05", "q06"]
    )

    assert refused.exit_code == 2 and "partial_overlap" in refused.output
    assert allowed.exit_code == 1 and "regressed cases (2): q05, q06" in allowed.output


def test_a_baseline_scored_by_another_scorer_version_is_incomparable(repo: Path) -> None:
    base = set_baseline(repo, [])
    run_json = base / "run.json"
    data = json.loads(run_json.read_text())
    data["scorers"][0]["version"] = "0"
    run_json.write_text(json.dumps(data))

    result = gate_run(repo, RELATIVE)

    assert result.exit_code == 2
    assert "incomparable with the baseline" in result.output


def test_baseline_set_writes_the_suite_to_run_id_mapping(repo: Path) -> None:
    base = set_baseline(repo, [])
    again = runner.invoke(app, ["baseline", "set", str(base)])

    assert yaml.safe_load((repo / "evals" / "baselines.yaml").read_text()) == {
        "demo-suite": base.name
    }
    assert f"commit {base} and baselines.yaml" in " ".join(again.output.split())


def test_compare_prints_regressions_first(repo: Path) -> None:
    base = set_baseline(repo, ["q09"])
    s = suite(repo, [{"metric": "exact_match", "min": 0}], ["q02"])
    runner.invoke(app, ["run", str(s)])
    cand = next(d for d in run_dirs(repo) if d != base)

    result = runner.invoke(app, ["compare", str(base), str(cand)])

    assert result.exit_code == 0, result.output
    lines = [line.strip() for line in result.output.splitlines() if line.startswith("    ")]
    assert lines[0].startswith("regressed q02") and lines[-1].startswith("fixed")


# ---------------------------------------------------------------- pytest


def test_assert_gate_raises_an_assertion_naming_the_regressed_cases(repo: Path) -> None:
    set_baseline(repo, [])
    results = run_suite(suite(repo, RELATIVE, ["q04", "q08"]), gate=True)

    with pytest.raises(GateFailed, match=r"regressed cases \(2\): q04, q08") as info:
        assert_gate(results)
    assert isinstance(info.value, AssertionError)


def test_assert_gate_passes_silently_and_raises_run_and_config_errors(repo: Path) -> None:
    assert_gate(run_suite(suite(repo, [{"metric": "exact_match", "min": 0.5}]), gate=True))

    with pytest.raises(RunError):
        no_calls = suite(
            repo, [{"metric": "exact_match", "min": 0}], budget={"max_calls": 0}, cache="off"
        )
        assert_gate(run_suite(no_calls))
    with pytest.raises(ConfigError):
        assert_gate(run_suite(suite(repo, None)))
