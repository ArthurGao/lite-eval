"""Subprocess targets against a scripted local program (design §18.11, §18.23)."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from lite_eval.contracts import Case
from lite_eval.errors import RunError
from lite_eval.runner import RunResult, prepare_targets
from lite_eval.store import load_run
from lite_eval.suite import load_suite
from lite_eval.targets import command
from lite_eval.targets.base import TargetError, current_repeat
from lite_eval.targets.command import STDERR_LOG, CommandSpec, CommandTarget
from lite_eval.targets.fake import FakeTarget
from tests.helpers import demo_dataset, execute_one, output_errors, run_prepared, write_suite

PROGRAM = str(Path(__file__).parents[1] / "data" / "command_target.py")


def cases(n: int) -> list[dict[str, Any]]:
    return [{"id": f"q{i}", "input": {"i": i}, "expected": f"answer {i}"} for i in range(1, n + 1)]


def command_suite(root: Path, *flags: str, n: int = 3, **spec: Any) -> Path:
    command: dict[str, Any] = {"argv": [sys.executable, PROGRAM, *flags], **spec.pop("command", {})}
    return write_suite(
        root,
        cases=cases(n),
        targets=[{"command": command, "configs": [{"name": "a", "level": 2}]}],
        scorers=["exact_match"],
        **spec,
    )


def stderr_log(result: RunResult) -> str:
    return (result.directory / STDERR_LOG).read_text()


def test_concurrency_4_maps_every_reply_to_its_case(tmp_path: Path) -> None:
    result = run_prepared(command_suite(tmp_path, "--jitter", n=8, concurrency=4))
    assert result.record.status == "complete"
    assert result.record.summary.metrics["exact_match"].value == 1.0  # each got its own answer
    assert set(output_errors(result).values()) == {None}


async def test_concurrent_calls_on_the_target_are_serialised(tmp_path: Path) -> None:
    """The target's own lock, without the runner's gate: 24 overlapping calls, each answered."""
    target = CommandTarget(CommandSpec(argv=[sys.executable, PROGRAM, "--jitter"]), {}, tmp_path)
    all_cases = [Case(id=c["id"], input=c["input"], expected=c["expected"]) for c in cases(8)]

    async def one(case: Case, repeat: int) -> tuple[str, int, Any]:
        current_repeat.set(repeat)
        out = await target.run(case)
        return out.case_id, out.repeat, out.value

    await target.setup(demo_dataset(tmp_path / "r"))
    try:
        # Every call finishes before teardown, errors included: a failing call must not leave
        # the others starting processes while the target is torn down (the test would hang).
        got = await asyncio.gather(
            *(one(c, r) for c in all_cases for r in range(3)), return_exceptions=True
        )
    finally:
        await target.teardown()
    assert got == [(c.id, r, c.expected) for c in all_cases for r in range(3)]


def test_the_config_reaches_the_program_and_the_record(tmp_path: Path) -> None:
    result = run_prepared(command_suite(tmp_path, "--echo-config"))
    outputs = load_run(result.directory).outputs
    assert {str(o.value) for o in outputs} == {"{'level': 2}"}
    command = result.record.target.config["command"]
    assert isinstance(command, dict)
    assert command["argv"] == [sys.executable, PROGRAM, "--echo-config"]
    assert command["cwd"] is None  # as written in the suite: no resolved local path


@pytest.mark.parametrize(
    ("flags", "extra", "kind", "message"),
    [
        (["--wrong-id-on", "q2"], {}, "invalid_output", "reply for 'someone-else' repeat 0"),
        (["--garbage-on", "q2"], {}, "invalid_output", "not a reply line"),
        (
            ["--big-on", "q2"],
            {"command": {"max_line_bytes": 1000}},
            "truncated",
            "max_line_bytes (1000)",
        ),
        (["--sleep-on", "q2"], {"timeout_s": 0.5}, "timeout", "no reply within 0.5s"),
        (["--error-on", "q2"], {}, "truncated", "model hit max_tokens"),
    ],
)
def test_a_bad_reply_fails_its_case_and_the_next_case_still_runs(
    tmp_path: Path, flags: list[str], extra: dict[str, Any], kind: str, message: str
) -> None:
    result = run_prepared(command_suite(tmp_path, *flags, **extra))
    got = output_errors(result)
    assert got["q1"] is None and got["q3"] is None
    assert got["q2"] is not None
    assert got["q2"][0] == kind
    assert message in got["q2"][1]
    assert result.record.status == "complete"


def test_out_of_step_replies_restart_the_process_uncounted(tmp_path: Path) -> None:
    # A timeout, then a wrong id: two restarts, and a later crash still gets its one restart.
    state = tmp_path / "starts"
    result = run_prepared(
        command_suite(
            tmp_path,
            "--sleep-on", "q2", "--sleep-s", "2",
            "--wrong-id-on", "q3",
            "--crash-on", "q4", "--crash-starts", "3", "--state", str(state),
            n=5,
            timeout_s=0.5,
        )
    )  # fmt: skip
    got = output_errors(result)
    assert [e[0] if (e := got[c]) else None for c in sorted(got)] == [
        None,
        "timeout",
        "invalid_output",
        "exception",
        None,
    ]
    assert result.record.status == "complete"
    assert state.read_text() == "4"  # first start, two out-of-step restarts, one crash restart
    assert stderr_log(result).count("restarted after a timeout or a reply out of step") == 2


def test_a_crash_restarts_once(tmp_path: Path) -> None:
    state = tmp_path / "starts"
    result = run_prepared(
        command_suite(tmp_path, "--crash-on", "q2", "--crash-starts", "1", "--state", str(state))
    )
    got = output_errors(result)
    assert got["q2"] == ("exception", "the target process exited (code 3)")
    assert got["q1"] is None and got["q3"] is None
    assert result.record.status == "complete"
    log = stderr_log(result)
    assert "process exited (code 3): restarting once" in log
    assert "restarted after the process exited" in log
    assert "still running" not in log  # a restart kills at once, without waiting notes


def test_a_second_crash_abandons_the_rest_and_the_run_is_partial(tmp_path: Path) -> None:
    result = run_prepared(command_suite(tmp_path, "--crash-on", "q2,q4", n=5))
    got = output_errors(result)
    assert got["q1"] is None and got["q3"] is None
    assert got["q2"] == ("exception", "the target process exited (code 3)")
    assert (
        got["q4"] is not None and "exited twice (last exit code 3); not restarted" in got["q4"][1]
    )
    assert got["q5"] is not None and got["q5"][1].startswith("not run: ")
    rec = result.record
    assert rec.status == "partial"
    assert rec.summary.outputs_expected == 5  # every case stays in the denominator
    assert [w.code for w in rec.warnings if w.code == "target_abandoned"] == ["target_abandoned"]


def test_a_restart_that_never_gets_ready_counts_as_an_exit(tmp_path: Path) -> None:
    state = tmp_path / "starts"
    path = command_suite(
        tmp_path,
        "--crash-on", "q2", "--ready-starts", "1", "--state", str(state),
        n=4,
        command={"startup_timeout_s": 0.5},
    )  # fmt: skip
    got = output_errors(run_prepared(path))
    assert got["q1"] is None
    assert got["q2"] == ("exception", "the target process exited (code 3)")
    assert got["q3"] is not None and "exited twice" in got["q3"][1]  # the restart hung
    assert got["q4"] is not None and got["q4"][1].startswith("not run: ")
    assert state.read_text() == "2"  # no third start


def test_stderr_is_kept_redacted_in_the_run_directory(tmp_path: Path) -> None:
    result = run_prepared(
        command_suite(
            tmp_path,
            "--stderr",
            "loading weights with tok-1234abcd",
            redact=[{"name": "token", "pattern": "tok-[0-9a-f]+"}],
        )
    )
    log = stderr_log(result)
    assert "loading weights with" in log
    assert "tok-1234abcd" not in log


def test_only_allowed_environment_variables_reach_the_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITE_EVAL_TEST_SECRET", "x")
    monkeypatch.setenv("LITE_EVAL_TEST_ALLOWED", "y")
    path = command_suite(
        tmp_path, "--env", command={"env_allow": ["PATH", "LITE_EVAL_TEST_ALLOWED"]}
    )
    outputs = load_run(run_prepared(path).directory).outputs
    names = outputs[0].value
    assert isinstance(names, list)
    assert "LITE_EVAL_TEST_ALLOWED" in names and "PATH" in names
    assert "LITE_EVAL_TEST_SECRET" not in names


def test_a_program_that_never_gets_ready_fails_setup(tmp_path: Path) -> None:
    path = command_suite(tmp_path / "a", "--no-ready", command={"startup_timeout_s": 0.5})
    with pytest.raises(RunError, match=r"setup of config 'a' failed: no \{\"ready\": true\}"):
        run_prepared(path)
    path = command_suite(tmp_path / "b", "--bad-ready")
    with pytest.raises(RunError, match=r"first line was not \{\"ready\": true\}"):
        run_prepared(path)


def test_cases_waiting_for_the_process_do_not_use_up_their_timeout(tmp_path: Path) -> None:
    """Each answer takes 0.3 s, the timeout is 1 s, 6 cases at concurrency 4: queued on the
    process lock, the last cases would wait 1.5 s and time out; the runner sends one at a time."""
    result = run_prepared(
        command_suite(tmp_path, "--delay", "0.3", n=6, concurrency=4, timeout_s=1.0)
    )
    assert set(output_errors(result).values()) == {None}


def test_an_artifact_may_not_replace_a_record_file(tmp_path: Path) -> None:
    class Leaky(FakeTarget):
        def artifacts(self) -> dict[str, str]:
            return {"run.json": "{}"}

    with pytest.raises(ValueError, match=r"invalid artifact name .run\.json."):
        execute_one(tmp_path, "a", Leaky())
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").rglob("run.json"))


async def test_teardown_closes_stdin_then_terminates_then_kills(tmp_path: Path) -> None:
    target = CommandTarget(CommandSpec(argv=[sys.executable, PROGRAM, "--stubborn"]), {}, tmp_path)
    target.grace_s = 0.3
    await target.setup(demo_dataset(tmp_path / "r"))
    proc = target._proc
    assert proc is not None
    await target.teardown()
    assert proc.returncode == -9  # SIGKILL: it ignored both the closed stdin and SIGTERM
    log = target.artifacts()[STDERR_LOG]
    assert "still running 0.3s after closed stdin: terminate" in log
    assert "still running 0.3s after SIGTERM: kill" in log


async def test_teardown_of_a_well_behaved_program_needs_no_signal(tmp_path: Path) -> None:
    target = CommandTarget(CommandSpec(argv=[sys.executable, PROGRAM]), {}, tmp_path)
    await target.setup(demo_dataset(tmp_path / "r"))
    proc = target._proc
    assert proc is not None
    await target.teardown()
    assert proc.returncode == 0  # it read EOF on stdin and exited
    assert target.artifacts() == {}


def test_the_command_is_announced(tmp_path: Path) -> None:
    heard: list[str] = []
    prepare_targets(load_suite(command_suite(tmp_path)), announce=heard.append)
    assert heard == [f"config a: {sys.executable} {PROGRAM} (cwd {tmp_path.parent})"]


# ---------------------------------------------------------------- Codex E4a review


async def _gone(pid: int, within_s: float = 5.0) -> bool:
    """True once no process has this pid (an orphan is reaped by init)."""
    for _ in range(int(within_s / 0.05)):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.05)
    return False


def _case(cid: str) -> Case:
    return Case(id=cid, input={}, expected=f"answer {cid}")


async def test_an_exit_between_cases_restarts_without_failing_the_next_case(
    tmp_path: Path,
) -> None:
    target = CommandTarget(
        CommandSpec(argv=[sys.executable, PROGRAM, "--exit-after", "q1"]), {}, tmp_path
    )
    await target.setup(demo_dataset(tmp_path / "r"))
    try:
        assert (await target.run(_case("q1"))).value == "answer q1"
        await asyncio.sleep(0.5)  # the process has exited before the next case is sent
        assert (await target.run(_case("q2"))).value == "answer q2"  # sent to a fresh process
    finally:
        await target.teardown()
    log = target.artifacts()[STDERR_LOG]
    assert "process exited (code 0) between cases: restarting once" in log
    assert target.abandoned() is None


async def test_teardown_reaches_processes_the_command_started(tmp_path: Path) -> None:
    """A wrapper (`uv run python x.py`) leaves its child behind unless the group is signalled."""
    child_pid = tmp_path / "child.pid"
    target = CommandTarget(
        CommandSpec(argv=[sys.executable, PROGRAM, "--spawn-child", str(child_pid)]), {}, tmp_path
    )
    target.grace_s = 0.3
    await target.setup(demo_dataset(tmp_path / "r"))
    pid = int(child_pid.read_text())
    await target.teardown()
    assert await _gone(pid)


async def test_a_restart_reaches_processes_the_command_started(tmp_path: Path) -> None:
    child_pid = tmp_path / "child.pid"
    argv = [sys.executable, PROGRAM, "--spawn-child", str(child_pid), "--wrong-id-on", "q1"]
    target = CommandTarget(CommandSpec(argv=argv), {}, tmp_path)
    await target.setup(demo_dataset(tmp_path / "r"))
    first_child = int(child_pid.read_text())
    try:
        with pytest.raises(TargetError):
            await target.run(_case("q1"))  # out of step: the next case restarts
        await asyncio.wait_for(target.run(_case("q2")), 10)  # the restart does not stall
        assert await _gone(first_child)
    finally:
        await target.teardown()


async def test_a_failed_startup_leaves_no_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    spec = CommandSpec(
        argv=[sys.executable, PROGRAM, "--no-ready", "--pid-file", str(pid_file)],
        startup_timeout_s=0.5,
    )
    target = CommandTarget(spec, {}, tmp_path)
    with pytest.raises(TargetError, match="no"):
        await target.setup(demo_dataset(tmp_path / "r"))
    assert await _gone(int(pid_file.read_text()))


async def test_notes_survive_a_full_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command, "STDERR_CAP", 50)
    argv = [
        sys.executable,
        PROGRAM,
        "--stderr",
        "x" * 200,
        "--crash-on",
        "q1",
        "--crash-starts",
        "1",
    ]
    target = CommandTarget(CommandSpec(argv=argv), {}, tmp_path)
    await target.setup(demo_dataset(tmp_path / "r"))
    await asyncio.sleep(0.3)  # the stderr pump has filled the cap
    try:
        with pytest.raises(TargetError):
            await target.run(_case("q1"))
    finally:
        await target.teardown()
    log = target.artifacts()[STDERR_LOG]
    assert "process exited (code 3): restarting once" in log
    assert "more bytes of stderr not kept" in log


def test_allowed_environment_values_are_masked_wherever_the_program_repeats_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E4d (design §18.11, §18.12): an allowed variable named like a secret is masked by value;
    a program printing one (stderr, a reply) puts nothing in the record. PATH is kept."""
    token = "tok-0f1e2d3c4b5a69788796"
    monkeypatch.setenv("LITE_EVAL_TEST_TOKEN", token)
    path = command_suite(
        tmp_path,
        "--echo-env",
        "LITE_EVAL_TEST_TOKEN",
        "--echo-env",
        "PATH",
        command={"env_allow": ["PATH", "LITE_EVAL_TEST_TOKEN"]},
    )
    result = run_prepared(path)
    files = "".join(f.read_text() for f in result.directory.iterdir())
    assert token not in files
    stored = load_run(result.directory)
    marker = "«redacted:env:LITE_EVAL_TEST_TOKEN»"
    assert stored.outputs[0].value == {
        "LITE_EVAL_TEST_TOKEN": marker,
        "PATH": os.environ["PATH"],  # not a secret: kept as it is
    }
    assert f"LITE_EVAL_TEST_TOKEN={marker}" in (result.directory / "target.stderr.log").read_text()
    assert stored.record.redact.known == ["env:LITE_EVAL_TEST_TOKEN"]


LONG_TOKEN = "tok-" + "0123456789abcdef" * 4  # longer than pydantic's 24 + 24 character cut


def _leaked(token: str, text: str) -> list[str]:
    return [part for part in (token[:8], token[-8:]) if part in text]


def test_a_reply_that_is_not_a_reply_line_is_masked_before_it_is_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E4d /simplify: the line is cut to 500 bytes and pydantic's message shows a value's first
    and last 24 characters; masked afterwards, neither cut form is recognisable."""
    monkeypatch.setenv("LITE_EVAL_TEST_TOKEN", LONG_TOKEN)
    path = command_suite(
        tmp_path,
        "--leak-env",
        "LITE_EVAL_TEST_TOKEN",
        command={"env_allow": ["PATH", "LITE_EVAL_TEST_TOKEN"]},
    )
    result = run_prepared(path)
    files = "".join(f.read_text() for f in result.directory.iterdir())
    error = load_run(result.directory).outputs[0].error
    assert error is not None and error.kind == "invalid_output"
    assert _leaked(LONG_TOKEN, files + error.message) == []


def test_a_setup_failure_masks_the_program_s_secrets_in_its_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first line is quoted (cut to 200) in the setup error, which the CLI prints."""
    monkeypatch.setenv("LITE_EVAL_TEST_TOKEN", LONG_TOKEN)
    path = command_suite(
        tmp_path,
        "--ready-echo-env",
        "LITE_EVAL_TEST_TOKEN",
        command={"env_allow": ["PATH", "LITE_EVAL_TEST_TOKEN"]},
    )
    with pytest.raises(RunError) as failed:
        run_prepared(path)
    assert _leaked(LONG_TOKEN, str(failed.value)) == []
    assert "«redacted:env:LITE_EVAL_TEST_TOKEN»" in str(failed.value)


def test_secret_variables_are_named_like_secrets_unless_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Masking every allowed value would also hide ordinary ones (a host, a model name) in
    outputs and scores: by default only names like KEY, TOKEN, SECRET, PASS(WORD),
    CREDENTIAL, AUTH are secrets; `secret_env` lists them explicitly instead."""
    monkeypatch.setenv("LITE_EVAL_TEST_TOKEN", "tok-0f1e2d3c4b5a69788796")
    monkeypatch.setenv("LITE_EVAL_TEST_HOST", "127.0.0.1:11434")
    names = ["PATH", "LITE_EVAL_TEST_TOKEN", "LITE_EVAL_TEST_HOST"]
    flags = [x for n in names[1:] for x in ("--echo-env", n)]
    default = command_suite(tmp_path / "a", *flags, command={"env_allow": names})
    value = load_run(run_prepared(default).directory).outputs[0].value
    assert value == {
        "LITE_EVAL_TEST_TOKEN": "«redacted:env:LITE_EVAL_TEST_TOKEN»",
        "LITE_EVAL_TEST_HOST": "127.0.0.1:11434",
    }
    listed = command_suite(
        tmp_path / "b", *flags, command={"env_allow": names, "secret_env": ["LITE_EVAL_TEST_HOST"]}
    )
    value = load_run(run_prepared(listed).directory).outputs[0].value
    assert value == {
        "LITE_EVAL_TEST_TOKEN": "tok-0f1e2d3c4b5a69788796",
        "LITE_EVAL_TEST_HOST": "«redacted:env:LITE_EVAL_TEST_HOST»",
    }


def test_a_reply_that_is_not_utf8_is_invalid_output(tmp_path: Path) -> None:
    """Masking decodes each line first; a reply that is not UTF-8 is not repaired on the way."""
    path = command_suite(tmp_path, "--latin1-on", "q1")
    errors = output_errors(run_prepared(path))
    error = errors["q1"]
    assert error is not None and error[0] == "invalid_output" and "not UTF-8" in error[1]
    assert errors["q2"] is None


def test_stderr_cut_at_its_cap_keeps_no_start_of_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security review of 9d8e815: the cap cut stderr inside a secret, leaving its first part,
    which no later masking recognises. The tail that could start a secret is dropped too."""
    import lite_eval.targets.command as command

    token = "tok-" + "0123456789abcdef" * 4
    monkeypatch.setenv("LITE_EVAL_TEST_TOKEN", token)
    padding = "p" * 40
    monkeypatch.setattr(command, "STDERR_CAP", len(padding) + 1 + len("LITE_EVAL_TEST_TOKEN=") + 20)
    path = command_suite(
        tmp_path,
        "--stderr",
        padding,
        "--echo-env",
        "LITE_EVAL_TEST_TOKEN",
        command={"env_allow": ["PATH", "LITE_EVAL_TEST_TOKEN"]},
    )
    log = (run_prepared(path).directory / "target.stderr.log").read_text()
    assert token[:8] not in log and "more bytes of stderr not kept" in log
