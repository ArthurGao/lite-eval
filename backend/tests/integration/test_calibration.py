"""Calibrating a judge (design §9, §18.24): a judging-pack run of the judge itself, its agreement
with labels fixed by construction, per construction and as a confusion matrix; a suite's judge
names that run, and the runner refuses a judge whose calibration is missing, stale or too weak."""

from pathlib import Path
from typing import Any

import pytest

from lite_eval.analysis.judging import confusion
from lite_eval.contracts import Usage
from lite_eval.errors import ConfigError
from lite_eval.judges.fake import FakeJudge, by_marker
from lite_eval.judges.target import JudgeTarget
from lite_eval.runner import RunOptions, RunResult
from lite_eval.store import load_run
from tests.helpers import Sleeps, execute_suite

JUDGE: dict[str, Any] = {
    "provider": "ollama",
    "model": "qwen3:8b",
    "base_url": "http://127.0.0.1:1",
    "think": False,
    "billing": "free",
}
SOURCES = [{"text": "Pour-over: 1 gram of coffee to 16 grams of water."}]
MARK = "seventeen"  # what every constructed corruption below adds


def labelled(cid: str, answer: str, label: str, construction: str) -> dict[str, Any]:
    return {
        "id": cid,
        "input": {"question": "ratio?", "answer": answer, "sources": SOURCES},
        "expected": {"label": label},
        "tags": {"construction": construction},
    }


CASES = [
    labelled("c1", "1 gram to 16 grams [1]", "grounded", "none"),
    labelled("c2", "1 gram to 16 grams [1]", "grounded", "none"),
    labelled("c3", f"1 gram to {MARK} grams [1]", "unsupported", "change_number"),
    labelled("c4", f"Use {MARK} grams, always rinse the filter [1]", "unsupported", "insert"),
    # the fake judge misses this one: its corruption does not contain the marker
    labelled("c5", "1 gram to 16 grams [1]; brew at 80 degrees", "unsupported", "insert"),
]


def fake(spec: Any, timeout_s: float | None) -> FakeJudge:
    return FakeJudge(spec, by_marker(MARK, "unsupported", "grounded"))


def calibrate(root: Path, **judge: Any) -> RunResult:
    target = JudgeTarget(rubric="grounded", client_factory=fake, **{**JUDGE, **judge})
    (result,) = execute_suite(
        root,
        [("qwen-nothink", target)],
        RunOptions(sleep=Sleeps()),
        cases=CASES,
        pack="judging",
        scorers=[],
        repeats=1,
    )
    return result


def test_a_calibration_run_measures_agreement_per_construction_and_as_a_matrix(
    tmp_path: Path,
) -> None:
    result = calibrate(tmp_path)
    summary = result.record.summary
    assert summary.metrics["label_agreement"].value == pytest.approx(4 / 5)
    groups = summary.groups["construction"]
    assert groups["none"]["label_agreement"] == 1.0
    assert groups["change_number"]["label_agreement"] == 1.0
    assert groups["insert"]["label_agreement"] == pytest.approx(0.5)
    assert confusion(load_run(result.directory).scores) == {
        ("grounded", "grounded"): 2,
        ("unsupported", "unsupported"): 2,
        ("unsupported", "grounded"): 1,  # (expected, got): the missed corruption
    }
    # the judge is the target: its identity is the config a scorer's calibration is checked against
    config = result.record.target.config
    assert {k: config[k] for k in ("provider", "channel", "model", "temperature", "think")} == {
        "provider": "ollama",
        "channel": "ollama",
        "model": "qwen3:8b",
        "temperature": 0.0,
        "think": False,
    }
    assert config["rubric"] == "grounded" and len(str(config["prompt_version"])) == 12
    output = load_run(result.directory).outputs[0]
    assert output.value == {"label": "grounded", "reason": "fake", "evidence": []}
    assert output.trace.usage == Usage(input_tokens=100, output_tokens=10)


def test_an_unusable_reply_in_calibration_is_an_invalid_output(tmp_path: Path) -> None:
    def broken(spec: Any, timeout_s: float | None) -> FakeJudge:
        return FakeJudge(spec, lambda m: "{not json")

    target = JudgeTarget(rubric="grounded", client_factory=broken, **JUDGE)
    (result,) = execute_suite(
        tmp_path, [("q", target)], RunOptions(sleep=Sleeps()), cases=CASES[:1],
        pack="judging", scorers=[], repeats=1,
    )  # fmt: skip
    error = load_run(result.directory).outputs[0].error
    assert error is not None and error.kind == "invalid_output" and "not a verdict" in error.message


def judged(root: Path, calibration: dict[str, Any] | None, gate: Any = None, **judge: Any) -> Any:
    """A suite whose judge names `calibration`, run on two plain outputs."""
    params: dict[str, Any] = {"rubric": "grounded"}
    if calibration is not None:
        params["calibration"] = calibration
    cases = [
        {"id": "a", "input": {"question": "q"}, "expected": {"answer": "x", "sources": SOURCES}},
    ]
    from lite_eval.targets.fake import FakeTarget

    return execute_suite(
        root,
        [("a", FakeTarget())],
        RunOptions(judge_factory=fake, sleep=Sleeps()),
        cases=cases,
        scorers=[{"judge": params}],
        judges={"grounded": {**JUDGE, **judge}},
        **({"gate": gate} if gate is not None else {}),
    )


def calibration_of(result: RunResult, root: Path, min_agreement: float) -> dict[str, Any]:
    return {"run": str(result.directory.relative_to(root)), "min_agreement": min_agreement}


def test_a_judge_calibrated_by_a_matching_run_is_used_without_warning(tmp_path: Path) -> None:
    run = calibrate(tmp_path)
    (result,) = judged(tmp_path, calibration_of(run, tmp_path, 0.8))
    assert all(w.code != "judge_uncalibrated" for w in result.record.warnings)
    (ref,) = result.record.scorers
    assert ref.calibrated is True


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"min_agreement": 0.9}, r"agreement 0\.8000 < 0\.9"),
        ({"run": "runs/demo-suite/no-such-run"}, "calibration run .* not found"),
        ({"judge": {"think": True}}, r"calibrated a different judge: think False -> True"),
        ({"judge": {"model": "qwen3:14b"}}, r"model 'qwen3:8b' -> 'qwen3:14b'"),
    ],
)
def test_a_missing_weak_or_stale_calibration_refuses_to_run(
    tmp_path: Path, change: dict[str, Any], error: str
) -> None:
    run = calibrate(tmp_path)
    calibration = {**calibration_of(run, tmp_path, 0.8), **change}
    calibration.pop("judge", None)
    calls: list[Any] = []

    def counting(spec: Any, timeout_s: float | None) -> FakeJudge:
        calls.append(spec)
        return fake(spec, timeout_s)

    from lite_eval.targets.fake import FakeTarget

    target = FakeTarget()
    with pytest.raises(ConfigError, match=error):
        execute_suite(
            tmp_path,
            [("a", target)],
            RunOptions(judge_factory=counting, sleep=Sleeps()),
            cases=[
                {
                    "id": "a",
                    "input": {"question": "q"},
                    "expected": {"answer": "x", "sources": SOURCES},
                }
            ],
            scorers=[{"judge": {"rubric": "grounded", "calibration": calibration}}],
            judges={"grounded": {**JUDGE, **change.get("judge", {})}},
        )
    assert calls == [] and sum(target.calls.values()) == 0  # refused before any call


def test_an_uncalibrated_judge_runs_with_a_warning_but_cannot_be_gated(tmp_path: Path) -> None:
    (result,) = judged(tmp_path / "a", None)
    codes = {w.code: w.message for w in result.record.warnings}
    assert "judge_uncalibrated" in codes and "exploratory" in codes["judge_uncalibrated"]
    (ref,) = result.record.scorers
    assert ref.calibrated is False
    gate = [{"metric": '{"judge":{"rubric":"grounded"}}', "min": 0.5}]
    from lite_eval.api import assert_gate

    with pytest.raises(ConfigError, match="not calibrated: a gate cannot rely on it"):
        assert_gate(judged(tmp_path / "b", None, gate=gate))


def test_a_calibration_run_exports_a_kb_row_from_the_suite_s_kb_section(tmp_path: Path) -> None:
    import json

    from typer.testing import CliRunner

    from lite_eval.cli import app

    kb = {
        "eval": "2026-11-18-lite-rag-grounded-judge",
        "project": "lite-rag",
        "task": "judging",
        "dataset": "5 answers, 3 corrupted by construction",
        "grader": "constructed",
        "metrics": {"label_agreement": "accuracy"},
        "notes": "fake judge: the export's shape",
    }
    target = JudgeTarget(rubric="grounded", client_factory=fake, **JUDGE)
    (result,) = execute_suite(
        tmp_path, [("qwen-nothink", target)], RunOptions(sleep=Sleeps()), cases=CASES,
        pack="judging", scorers=[], repeats=1, kb=kb,
    )  # fmt: skip
    done = CliRunner().invoke(app, ["export", "kb", str(result.directory)])
    assert done.exit_code == 0, done.output
    (row,) = [json.loads(line) for line in done.output.splitlines()]
    assert {k: row[k] for k in ("vendor", "model", "effort", "variant", "metric", "value")} == {
        "vendor": "ollama",
        "model": "qwen3:8b",
        "effort": "n/a",  # thinking off
        "variant": "qwen-nothink",
        "metric": "accuracy",
        "value": 0.8,
    }
    assert (row["grader"], row["n"], row["trials"]) == ("constructed", 5, 1)
    no_kb = calibrate(tmp_path / "b")
    refused = CliRunner().invoke(app, ["export", "kb", str(no_kb.directory)])
    assert refused.exit_code == 2 and "no `kb:` section" in refused.output


def test_the_terminal_report_shows_the_matrix_and_the_judges_cost(tmp_path: Path) -> None:
    from lite_eval.report.terminal import render_run

    run = calibrate(tmp_path / "a")
    text = render_run(run.record, str(run.directory), run.scores)
    assert "  agreement matrix (expected -> judged):" in text
    assert "    grounded -> grounded: 2" in text
    assert "    unsupported -> grounded: 1" in text
    assert "    unsupported -> unsupported: 2" in text
    (judged_run,) = judged(tmp_path / "b", None)
    shown = render_run(judged_run.record)
    assert "  judges: 1 calls, tokens in 100 / out 10 (reasoning 0), $0.0000" in shown
    assert "agreement matrix" not in shown


def test_a_partial_calibration_run_vouches_for_nothing(tmp_path: Path) -> None:
    """A run the budget cut short measured agreement on some cases only."""
    import json

    run = calibrate(tmp_path)
    path = run.directory / "run.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "status": "partial"}))
    with pytest.raises(ConfigError, match=r"calibration run .* is partial"):
        judged(tmp_path, calibration_of(run, tmp_path, 0.8))


@pytest.mark.parametrize("bad", [-1, 1.5, float("nan"), float("inf"), "high"])
def test_a_threshold_outside_zero_to_one_is_a_config_error(tmp_path: Path, bad: Any) -> None:
    with pytest.raises(ConfigError, match="min_agreement must be a number from 0 to 1"):
        judged(tmp_path, {"run": "runs/x", "min_agreement": bad})
