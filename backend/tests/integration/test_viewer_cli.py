"""`lite-eval view`, `triage` and `report --html` (design §10.2, §11.2, §18.9, §18.12, §18.14)."""

import base64
import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lite_eval.annotations import current_annotations, read_annotations
from lite_eval.cli import app
from lite_eval.errors import ConfigError
from lite_eval.runner import RunResult
from lite_eval.targets.fake import FakeTarget, Step
from tests.helpers import execute_suite, write_static

runner = CliRunner()
SCRIPT_CASE = "</script><script>window.__pwned=1</script>"
SECRET = "sk-" + "B" * 24
CASES = [
    {"id": "q0", "input": {"query": SCRIPT_CASE}, "expected": "a0"},
    {"id": "q1", "input": {"query": "one"}, "expected": "a1"},
    {"id": "q2", "input": {"query": "two"}, "expected": "a2"},
]
JS = "console.log('</script>');"  # a bundle may hold this text: it must not end a tag


def build(tmp_path: Path) -> Path:
    """A stand-in for `pnpm build` output whose bundle holds a `</script>`."""
    return write_static(tmp_path / "static", js=JS)


@pytest.fixture
def no_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal test fails instead of hanging if the Viewer starts after all."""
    import uvicorn

    def started(*_: object, **__: object) -> None:
        raise AssertionError("the Viewer started")

    monkeypatch.setattr(uvicorn.Server, "run", started)


def run(
    root: Path, config: str, target: FakeTarget, redact: list[str], cases: list[Any] = CASES
) -> RunResult:
    (result,) = execute_suite(root, [(config, target)], cases=cases, redact=redact)
    return result


def leaking(*, secret_on: str, email_on: str | None = None) -> FakeTarget:
    steps = {secret_on: Step(value=f"key {SECRET}", echo_expected=False)}
    if email_on:
        steps[email_on] = Step(value="mail someone@example.com", echo_expected=False)
    return FakeTarget(steps=steps, config={"secret": secret_on, "email": email_on})


# ---------------------------------------------------------------- view


def test_view_check_serves_the_page_and_the_api(tmp_path: Path) -> None:
    result = runner.invoke(app, ["view", "--check", "--static", str(build(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "view check: / 200, /api/health 200" in result.output


def test_view_check_fails_without_a_frontend_build(tmp_path: Path) -> None:
    result = runner.invoke(app, ["view", "--check", "--static", str(tmp_path / "none")])
    assert result.exit_code == 3
    assert "/ 503" in result.output and "build_frontend.py" in result.output


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com", "::"])
def test_only_a_loopback_address_is_accepted(host: str) -> None:
    from lite_eval.server.run import check_host

    with pytest.raises(ConfigError, match="loopback only"):
        check_host(host)
    assert [check_host(h) for h in ("127.0.0.1", "::1", "localhost")] == [
        "127.0.0.1",
        "::1",
        "localhost",
    ]


@pytest.mark.usefixtures("no_serve")
def test_view_refuses_a_non_loopback_address_before_anything_else(tmp_path: Path) -> None:
    result = runner.invoke(app, ["view", "--host", "0.0.0.0", "--root", str(tmp_path)])
    assert result.exit_code == 2 and "loopback only" in result.output
    assert "Viewer on" not in result.output  # refused before it says it is listening
    assert not (tmp_path / ".lite-eval").exists()  # and before it opened the index


def test_the_evaluation_root_defaults_to_here_then_evals(tmp_path: Path) -> None:
    from lite_eval.server.run import resolve_root

    with pytest.raises(ConfigError, match="--root"):
        resolve_root(None, tmp_path)
    (tmp_path / "evals").mkdir()
    assert resolve_root(None, tmp_path) == tmp_path / "evals"
    (tmp_path / "runs").mkdir()
    assert resolve_root(None, tmp_path) == tmp_path
    assert resolve_root(tmp_path / "x", tmp_path) == tmp_path / "x"


@pytest.mark.usefixtures("no_serve")
def test_view_without_an_evaluation_root_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["view"])
    assert result.exit_code == 2 and "--root" in result.output


# ---------------------------------------------------------------- triage


def test_triage_walks_the_untriaged_misses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITE_EVAL_AUTHOR", "reviewer-1")
    target = FakeTarget(
        steps={i: Step(value="no", echo_expected=False) for i in ("q1", "q2")},
        config={"wrong": ["q1", "q2"]},
    )
    result = run(tmp_path, "a", target, [])

    first = runner.invoke(app, ["triage", str(result.directory)], input="r\nthe retriever\ns\n")
    assert first.exit_code == 0, first.output
    assert "q1" in first.output and "q2" in first.output
    assert "1 triaged, 1 skipped" in first.output
    (only,) = current_annotations(read_annotations(tmp_path)).values()
    assert (only.case_id, only.category, only.note, only.author, only.run_id) == (
        "q1",
        "real_miss",
        "the retriever",
        "reviewer-1",
        result.record.run_id,
    )

    second = runner.invoke(app, ["triage", str(result.directory)], input="l\n\n")
    assert second.exit_code == 0 and "q1" not in second.output  # already triaged
    assert {a.case_id: a.category for a in read_annotations(tmp_path)} == {
        "q1": "real_miss",
        "q2": "label_wrong",
    }
    assert "nothing to triage" in runner.invoke(app, ["triage", str(result.directory)]).output


def test_triage_stops_on_q(tmp_path: Path) -> None:
    target = FakeTarget(
        steps={i: Step(value="no", echo_expected=False) for i in ("q1", "q2")}, config={"w": 1}
    )
    result = run(tmp_path, "a", target, [])
    out = runner.invoke(app, ["triage", str(result.directory)], input="q\n")
    assert out.exit_code == 0 and read_annotations(tmp_path) == []
    assert "[1/2] q1" in out.output and "[2/2]" not in out.output  # the second is never shown


# ---------------------------------------------------------------- report --html


def embedded(html: str) -> dict[str, object]:
    match = re.search(
        r'<script type="application/json" id="lite-eval-data">(.*?)</script>', html, re.S
    )
    assert match is not None
    data: dict[str, object] = json.loads(match.group(1))
    return data


def test_a_run_export_is_one_self_contained_file(tmp_path: Path) -> None:
    result = run(tmp_path, "a", leaking(secret_on="q1"), ["api_keys"])
    out = tmp_path / "run.html"

    done = runner.invoke(
        app,
        [
            "report",
            "--html",
            str(result.directory),
            "-o",
            str(out),
            "--static",
            str(build(tmp_path)),
        ],
    )
    assert done.exit_code == 0, done.output
    html = out.read_text()

    assert "/assets/" not in html  # nothing left to fetch
    assert "<style>body{color:#111}</style>" in html
    src = re.search(r'<script type="module" src="data:text/javascript;base64,([^"]+)">', html)
    assert src is not None and base64.b64decode(src.group(1)).decode() == JS
    # only the data block and the module script end with </script>; case text cannot add one
    assert html.count("</script>") == 2
    assert "<script>window.__pwned" not in html  # the case text survives only escaped
    data = embedded(html)
    assert data["kind"] == "run"
    runs = data["runs"]
    assert isinstance(runs, dict)
    (exported,) = runs.values()
    case = exported["cases"]["q0"]
    assert case["content"]["case"]["input"] == {"query": SCRIPT_CASE}  # round-trips intact
    assert SECRET not in html
    assert exported["cases"]["q1"]["outputs"][0]["value"] == "key «redacted:api_keys»"
    assert [r["case_id"] for r in exported["rows"]["items"]] == ["q0", "q1", "q2"]


def test_an_export_adds_rules_but_never_removes_them(tmp_path: Path) -> None:
    result = run(tmp_path, "a", leaking(secret_on="q1", email_on="q2"), ["api_keys"])
    out = tmp_path / "run.html"
    static = build(tmp_path)

    args = ["report", "--html", str(result.directory), "-o", str(out), "--static", str(static)]
    assert runner.invoke(app, args).exit_code == 0
    assert "someone@example.com" in out.read_text() and SECRET not in out.read_text()

    assert runner.invoke(app, [*args, "--redact", "emails"]).exit_code == 0
    assert "someone@example.com" not in out.read_text() and SECRET not in out.read_text()


def test_a_comparison_export_uses_both_runs_rules(tmp_path: Path) -> None:
    # Each run's text must pass the other run's rules too: a leaks an email only b's rules
    # catch, b a key only a's rules catch.
    a = run(tmp_path, "a", leaking(secret_on="q1", email_on="q2"), ["api_keys"])
    b = run(tmp_path, "b", leaking(secret_on="q2", email_on="q1"), ["emails"])
    out = tmp_path / "compare.html"

    done = runner.invoke(
        app,
        [
            "report",
            "--html",
            f"{a.directory}..{b.directory}",
            "-o",
            str(out),
            "--static",
            str(build(tmp_path)),
        ],
    )
    assert done.exit_code == 0, done.output
    html = out.read_text()
    assert SECRET not in html and "someone@example.com" not in html
    data = embedded(html)
    assert data["kind"] == "compare"
    comparison = data["comparison"]
    assert isinstance(comparison, dict)
    assert (comparison["baseline_run"], comparison["candidate_run"]) == (
        a.record.run_id,
        b.record.run_id,
    )
    runs = data["runs"]
    assert isinstance(runs, dict) and len(runs) == 2


def test_an_export_without_a_frontend_build_is_a_config_error(tmp_path: Path) -> None:
    result = run(tmp_path, "a", leaking(secret_on="q1"), [])
    done = runner.invoke(
        app,
        [
            "report",
            "--html",
            str(result.directory),
            "-o",
            str(tmp_path / "x.html"),
            "--static",
            str(tmp_path / "none"),
        ],
    )
    assert done.exit_code == 2 and "build_frontend.py" in done.output


def test_redaction_never_rewrites_ids_in_an_export(tmp_path: Path) -> None:
    """A suite rule that matches a case id redacts text, not the keys the page navigates by."""
    cases = [{"id": "T-1", "input": "ticket", "expected": "ok"}]
    target = FakeTarget(steps={"T-1": Step(value="see T-1", echo_expected=False)}, config={})
    (result,) = execute_suite(
        tmp_path,
        [("a", target)],
        cases=cases,
        redact=[{"name": "ticket", "pattern": "T-[0-9]+"}],
    )
    out = tmp_path / "run.html"
    args = ["report", "--html", str(result.directory), "-o", str(out)]
    assert runner.invoke(app, [*args, "--static", str(build(tmp_path))]).exit_code == 0

    runs = embedded(out.read_text())["runs"]
    assert isinstance(runs, dict)
    (exported,) = runs.values()
    case = exported["cases"]["T-1"]
    assert case["case_id"] == "T-1" and exported["rows"]["items"][0]["case_id"] == "T-1"
    assert case["outputs"][0]["value"] == "see «redacted:ticket»"


def test_a_comparison_export_takes_each_run_from_its_own_root(tmp_path: Path) -> None:
    """Two checkouts of a repo, one run each: the candidate is not looked up in the
    baseline's root."""
    a = run(tmp_path / "main", "a", leaking(secret_on="q1"), [])
    b = run(tmp_path / "branch", "b", leaking(secret_on="q2"), [])
    out = tmp_path / "compare.html"

    done = runner.invoke(
        app,
        [
            "report",
            "--html",
            f"{a.directory}..{b.directory}",
            "-o",
            str(out),
            "--static",
            str(build(tmp_path)),
        ],
    )
    assert done.exit_code == 0, done.output
    comparison = embedded(out.read_text())["comparison"]
    assert isinstance(comparison, dict)
    assert (comparison["baseline_run"], comparison["candidate_run"]) == (
        a.record.run_id,
        b.record.run_id,
    )
    assert [(c["case_id"], c["kind"]) for c in comparison["changes"]] == [
        ("q2", "regressed"),
        ("q1", "fixed"),
    ]
