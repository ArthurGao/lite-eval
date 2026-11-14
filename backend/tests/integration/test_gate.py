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
from lite_eval.analysis.gate import metric_value
from lite_eval.api import gate_results
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
    def __init__(self, wrong=(), latency_ms=10.0, fail=()):
        self.wrong, self.latency_ms, self.fail = list(wrong), latency_ms, list(fail)
    def config(self): return {"wrong": self.wrong, "latency_ms": self.latency_ms, "fail": self.fail}
    def versions(self): return {"answers": "1"}
    def info(self): return {}
    def billing(self): return "free"
    def setup_identity(self): return {}
    async def setup(self, dataset): pass
    async def teardown(self): pass
    async def run(self, case):
        CALLS.append(case.id)
        if case.id in self.fail:
            raise RuntimeError("the service is down")
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


def test_every_baseline_switch_is_logged(repo: Path) -> None:
    from lite_eval.store import read_baseline_changes

    first = set_baseline(repo, wrong=[])
    s = suite(repo, [{"metric": "exact_match", "min": 0}], ["q01"])
    assert runner.invoke(app, ["run", str(s)]).exit_code == 0
    (second,) = [d for d in run_dirs(repo) if d != first]
    assert runner.invoke(app, ["baseline", "set", str(second)]).exit_code == 0

    changes = read_baseline_changes(repo / "evals")
    assert [(c.suite, c.run_id, c.previous) for c in changes] == [
        ("demo-suite", first.name, None),
        ("demo-suite", second.name, first.name),
    ]
    assert changes[0].set_at <= changes[1].set_at
    assert yaml.safe_load((repo / "evals" / "baselines.yaml").read_text()) == {
        "demo-suite": second.name
    }


# ---------------------------------------------------------------- E4d: rules per config, errors


def two_configs(repo: Path, gate: Any) -> Path:
    return write_suite(
        repo / "evals",
        cases=CASES,
        targets=[
            {
                "python": "answers_target:Answers",
                "configs": [{"name": "plain"}, {"name": "rough", "wrong": ["q01", "q02", "q03"]}],
            }
        ],
        gate=gate,
    )


def test_a_rule_scoped_to_some_configs_judges_only_those(repo: Path) -> None:
    """llm-steward P8: only the run without rerank is gated; the other is reported."""
    s = two_configs(repo, [{"metric": "exact_match", "min": 0.9, "configs": ["plain"]}])
    result = runner.invoke(app, ["run", str(s), "--gate"])

    assert result.exit_code == 0, result.output  # rough scores 0.85: not its rule
    assert "gate passed: plain" in result.output
    assert "no gate rule applies to config rough" in result.output
    outcomes = {o.config: o for o in gate_results(run_suite(s))}
    assert (outcomes["plain"].applied, outcomes["rough"].applied) == (1, 0)


def test_a_rule_naming_an_unknown_config_is_refused_when_the_suite_loads(repo: Path) -> None:
    s = two_configs(repo, [{"metric": "exact_match", "min": 0.9, "configs": ["plian"]}])
    for args in (["run", str(s)], ["run", str(s), "--gate"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2
        assert "gate rule names config(s) ['plian'], not in this suite" in result.output
    assert calls() == []


@pytest.mark.parametrize(
    ("gate", "fail", "message"),
    [
        ([{"metric": "errors", "max": 0}], ["q03"], "errors = 1.0000 > max 0"),
        ([{"metric": "errors", "max": 0}], [], None),
        ([{"metric": "error_rate", "max": 0.04}], ["q03"], "error_rate = 0.0500 > max 0.04"),
        ([{"metric": "error_rate", "max": 0.05}], ["q03"], None),
    ],
)
def test_errored_cases_can_be_gated(
    repo: Path, gate: Any, fail: list[str], message: str | None
) -> None:
    s = write_suite(
        repo / "evals",
        cases=CASES,
        targets=[{"python": "answers_target:Answers", "configs": [{"name": "c", "fail": fail}]}],
        gate=gate,
    )
    result = runner.invoke(app, ["run", str(s), "--gate"])

    assert result.exit_code == (1 if message else 0), result.output
    if message:
        assert message in result.output


def test_scoring_errors_are_a_gate_metric_and_run_metrics_are_absolute_only(repo: Path) -> None:
    s = suite(repo, [{"metric": "scoring_errors", "max": 0}])
    (run,) = run_suite(s)
    record = run.record.model_copy(
        update={"summary": run.record.summary.model_copy(update={"scoring_errors": {"x": 2}})}
    )
    assert metric_value(record, "scoring_errors") == 2.0
    assert metric_value(run.record, "scoring_errors") == 0.0
    result = gate_run(repo, [{"metric": "errors", "compare_to": "baseline", "max_drop": 1}])
    assert result.exit_code == 2
    assert "errors cannot be compared to the baseline" in result.output


def test_a_gate_that_judges_none_of_the_runs_is_a_configuration_error(repo: Path) -> None:
    """Codex E4d: rules scoped to configs not run (`--config rough`) judge nothing; that is not
    a pass. One unjudged config next to a judged one is fine (llm-steward's rerank)."""
    s = two_configs(repo, [{"metric": "exact_match", "min": 0.9, "configs": ["plain"]}])
    result = runner.invoke(app, ["run", str(s), "--gate", "--config", "rough"])
    assert result.exit_code == 2
    assert "no gate rule applies to any of these runs" in result.output
    results = run_suite(s)
    with pytest.raises(ConfigError, match="no gate rule applies to any of these runs"):
        assert_gate([r for r in results if r.record.target.config["name"] == "rough"])
    assert_gate(results)  # plain judged and passing, rough not judged: passes
