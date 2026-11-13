"""`lite-eval run` and `rescore` through the real CLI (design §11.2, §18.4 exit codes)."""

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lite_eval.cli import app
from lite_eval.store import load_run
from tests.helpers import write_suite

runner = CliRunner()

TARGET_MODULE = """
from lite_eval.contracts import Output, Trace, Usage

class Echo:
    name = "echo"
    def __init__(self, upper=False, billing="free"):
        self.upper, self._billing = upper, billing
    def config(self): return {"upper": self.upper}
    def versions(self): return {"echo": "1"}
    def info(self): return {}
    def billing(self): return self._billing
    def setup_identity(self): return {}
    async def setup(self, dataset): pass
    async def teardown(self): pass
    async def run(self, case):
        value = case.expected.upper() if self.upper else case.expected
        return Output(case_id=case.id, value=value, trace=Trace(usage=Usage(input_tokens=3)))
"""


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Path]:
    """A user's repository: a target module at its root and an evaluation root in evals/."""
    (tmp_path / "echo_target.py").write_text(TARGET_MODULE)
    yield tmp_path
    sys.modules.pop("echo_target", None)


def _suite(repo: Path, **spec: Any) -> Path:
    return write_suite(
        repo / "evals",
        targets=[
            {
                "python": "echo_target:Echo",
                "configs": [{"name": "plain"}, {"name": "loud", "upper": True}],
            }
        ],
        **spec,
    )


def _run_dirs(repo: Path) -> list[Path]:
    return sorted((repo / "evals" / "runs" / "demo-suite").iterdir())


def test_run_writes_one_run_per_config_and_exits_zero(repo: Path) -> None:
    result = runner.invoke(app, ["run", str(_suite(repo))])

    assert result.exit_code == 0, result.output
    assert result.output.count("[complete]") == 2
    assert "1 case = 0.333 of a 0-1 metric" in result.output
    records = [load_run(d).record for d in _run_dirs(repo)]
    assert sorted(str(r.target.config["name"]) for r in records) == ["loud", "plain"]
    by_name = {r.target.config["name"]: r for r in records}
    assert by_name["plain"].summary.metrics["exact_match"].value == 1.0
    # Upper-casing breaks "Wellington" and "blue" but not "4".
    assert by_name["loud"].summary.metrics["exact_match"].value == pytest.approx(1 / 3)


def test_run_one_config(repo: Path) -> None:
    result = runner.invoke(app, ["run", str(_suite(repo)), "--config", "loud"])

    assert result.exit_code == 0, result.output
    assert [load_run(d).record.target.config["name"] for d in _run_dirs(repo)] == ["loud"]


def test_rescore_never_touches_the_target(repo: Path) -> None:
    suite = _suite(repo)
    assert runner.invoke(app, ["run", str(suite), "--config", "plain"]).exit_code == 0
    (original,) = _run_dirs(repo)
    # From here on, importing or calling the target fails.
    (repo / "echo_target.py").write_text("raise RuntimeError('the target must not be imported')\n")
    sys.modules.pop("echo_target", None)

    result = runner.invoke(
        app, ["rescore", str(original), "--scorer", '{"name": "contains", "case_sensitive": false}']
    )

    assert result.exit_code == 0, result.output
    new_dir = next(d for d in _run_dirs(repo) if d != original)
    new, old = load_run(new_dir), load_run(original)
    assert new.record.rescored_from == old.record.run_id
    assert new.outputs == old.outputs
    assert [s.name for s in new.record.scorers] == ['{"contains":{"case_sensitive":false}}']
    assert new.record.cost.calls == 0
    assert new.record.summary.metrics['{"contains":{"case_sensitive":false}}'].value == 1.0


def test_rescore_refuses_a_changed_dataset(repo: Path) -> None:
    suite = _suite(repo)
    runner.invoke(app, ["run", str(suite), "--config", "plain"])
    (original,) = _run_dirs(repo)
    cases = repo / "evals" / "datasets" / "demo" / "cases.jsonl"
    cases.write_text(cases.read_text().replace("Wellington", "Auckland"))

    result = runner.invoke(app, ["rescore", str(original)])

    assert result.exit_code == 2
    assert "dataset changed since run" in result.output


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"unknown_field": 1}, "unknown_field"),
        ({"scorers": ["nope"]}, "unknown scorer 'nope'"),
        ({"dataset": "../datasets/missing"}, "not a dataset directory"),
        ({"redact": [{"name": "ticket"}]}, "pattern"),
        ({"pack": "summarise"}, "unknown pack 'summarise'"),
    ],
)
def test_config_errors_exit_two(repo: Path, spec: dict[str, Any], message: str) -> None:
    result = runner.invoke(app, ["run", str(_suite(repo, **spec))])

    assert result.exit_code == 2
    assert message in result.output


def test_a_run_with_no_scored_case_exits_three(repo: Path) -> None:
    result = runner.invoke(app, ["run", str(_suite(repo, budget={"max_calls": 0}))])

    assert result.exit_code == 3
    assert "[failed]" in result.output


def test_ci_env_turns_on_the_billing_guard(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = write_suite(
        repo / "evals",
        targets=[
            {"python": "echo_target:Echo", "configs": [{"name": "paid", "billing": "per_token"}]}
        ],
    )
    monkeypatch.setenv("CI", "true")

    result = runner.invoke(app, ["run", str(suite)])

    assert result.exit_code == 2
    assert "CI mode refuses targets not declared free: paid (per_token)" in result.output
    assert not (repo / "evals" / "runs").exists()


def test_ci_mode_ignores_a_local_env_file(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = write_suite(
        repo / "evals",
        targets=[
            {"python": "echo_target:Echo", "configs": [{"name": "paid", "billing": "per_token"}]}
        ],
        allow_paid_in_ci=True,
    )
    (repo / ".env").write_text("LITE_EVAL_ALLOW_PAID=1\n")
    (repo / "evals" / ".env").write_text("LITE_EVAL_ALLOW_PAID=1\n")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("LITE_EVAL_ALLOW_PAID", raising=False)

    result = runner.invoke(app, ["run", str(suite), "--ci"])

    assert result.exit_code == 2


def test_run_json_is_plain_json_with_the_suite_path_relative(repo: Path) -> None:
    runner.invoke(app, ["run", str(_suite(repo)), "--config", "plain"])
    (run_dir,) = _run_dirs(repo)

    data = json.loads((run_dir / "run.json").read_text())
    assert data["suite"]["path"] == "suites/s.yaml"
    assert str(repo) not in (run_dir / "run.json").read_text()


def test_rescore_works_for_a_suite_outside_the_evaluation_root(repo: Path) -> None:
    suite = _suite(repo)
    elsewhere = repo / "other-root"
    run_result = runner.invoke(
        app, ["run", str(suite), "--config", "plain", "--root", str(elsewhere)]
    )
    assert run_result.exit_code == 0, run_result.output
    (original,) = sorted((elsewhere / "runs" / "demo-suite").iterdir())
    recorded = json.loads((original / "run.json").read_text())["suite"]["path"]

    result = runner.invoke(app, ["rescore", str(original)])

    assert recorded == "../evals/suites/s.yaml"  # relative: no absolute local path
    assert result.exit_code == 0, result.output


def test_a_run_directory_with_a_duplicated_output_is_refused(repo: Path) -> None:
    runner.invoke(app, ["run", str(_suite(repo)), "--config", "plain"])
    (run_dir,) = _run_dirs(repo)
    outputs = run_dir / "outputs.jsonl"
    first = outputs.read_text().splitlines()[0]
    outputs.write_text(outputs.read_text() + first + "\n")

    result = runner.invoke(app, ["rescore", str(run_dir)])

    assert result.exit_code == 2
    assert "duplicate output for case 'q1' repeat 0" in result.output
