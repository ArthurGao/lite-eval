"""Subprocess targets: one long-running process, one JSON line per case (design §18.11, §18.23).

A suite is trusted local configuration: it runs the command it names. The process is started from
an argv list (no shell) with only the environment variables in `env_allow`, and must print
`{"ready": true}` within `startup_timeout_s`. Per case it reads `{"case", "repeat", "config"}` on
stdin and answers one line `{"case_id", "repeat", "output", "trace"?, "error"?}` on stdout.

- One case at a time: a lock covers write-a-line / read-a-line, and `serial = True` tells the
  runner to send one case at a time, so cases waiting for the lock do not use up their timeout.
- A case is in flight from writing its request until its reply is read and checked. If it ends any
  other way (a timeout, another case's id, not JSON, a line over `max_line_bytes`) stdout may be
  out of step: the process is restarted before the next case. These restarts are not counted.
- A process that exits on its own is restarted once; when it exits a second time the target gives
  up: every remaining case is an `exception` and `abandoned()` tells the runner the run is partial.
- stderr is kept (at most 5 MB, the rest counted) and written, redacted, to the run directory as
  `target.stderr.log` (`artifacts()`).
- The process runs in its own process group; restarts and teardown signal the whole group, so a
  wrapper (`uv run python x.py`) leaves no child behind.
- teardown: close stdin, wait, terminate, wait, kill.
"""

import asyncio
import contextlib
import json
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from lite_eval.contracts import Case, ErrorInfo, Output, Trace
from lite_eval.targets.base import MB, SpecBase, SpecIdentity, TargetError, current_repeat

if TYPE_CHECKING:
    from lite_eval.datasets.local import Dataset

STDERR_LOG = "target.stderr.log"
STDERR_CAP = 5 * MB


class CommandSpec(SpecBase):
    """`targets: [{command: {...}, configs: [...]}]` in a suite."""

    argv: list[str] = Field(min_length=1)
    cwd: str | None = None  # relative to the suite file; default: the repository root
    env_allow: list[str] = Field(default_factory=lambda: ["PATH"])
    startup_timeout_s: float = Field(default=30.0, gt=0)
    max_line_bytes: int = Field(default=10 * MB, gt=0)


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    repeat: int
    output: JsonValue = None
    trace: Trace = Field(default_factory=Trace)
    error: ErrorInfo | None = None


class _Exited(Exception):
    """The process exited (stdout or stdin closed), or a restart never became ready."""


class CommandTarget(SpecIdentity):
    name = "command"
    serial = True  # the runner sends one case at a time (see the module docstring)

    def __init__(self, spec: CommandSpec, params: dict[str, JsonValue], cwd: Path) -> None:
        self.spec: CommandSpec = spec
        self.params = params
        self.cwd = cwd
        self.grace_s = 5.0  # teardown waits this long after closing stdin, and after SIGTERM
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr = bytearray()
        self._stderr_dropped = 0
        self._lock = asyncio.Lock()
        self._in_flight = False  # a case's exchange did not finish: stdout may be out of step
        self._restarted = False  # the one restart after an exit is used
        self._abandoned: str | None = None

    # -------------------------------------------------------------- identity

    def describe(self) -> str:
        return f"{' '.join(self.spec.argv)} (cwd {self.cwd})"

    def config(self) -> dict[str, Any]:
        s = self.spec
        # cwd as written in the suite: records hold no absolute local paths
        return {**self.params, "command": {"argv": s.argv, "cwd": s.cwd, "env_allow": s.env_allow}}

    def abandoned(self) -> str | None:
        """Why the target stopped running cases, or None (the runner marks the run partial)."""
        return self._abandoned

    def artifacts(self) -> dict[str, str]:
        """Files for the run directory; the runner redacts them before writing."""
        text = self._stderr.decode("utf-8", errors="replace")
        if self._stderr_dropped:
            text += f"\n[lite-eval] {self._stderr_dropped} more bytes of stderr not kept\n"
        return {STDERR_LOG: text} if text else {}

    # -------------------------------------------------------------- process

    def _note(self, text: str) -> None:
        # Not capped: notes are few and short, and they explain what happened to the process.
        self._stderr += f"[lite-eval] {text}\n".encode()

    def _keep(self, chunk: bytes) -> None:
        room = max(STDERR_CAP - len(self._stderr), 0)
        self._stderr += chunk[:room]
        self._stderr_dropped += max(len(chunk) - room, 0)

    async def _pump_stderr(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(65536):
            self._keep(chunk)

    async def _start(self) -> None:
        env = {k: os.environ[k] for k in self.spec.env_allow if k in os.environ}
        self._proc = await asyncio.create_subprocess_exec(
            *self.spec.argv,
            cwd=self.cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=self.spec.max_line_bytes + 1,  # +1: the newline
            start_new_session=True,  # its own process group: signals reach its children too
        )
        assert self._proc.stderr is not None
        self._stderr_task = asyncio.create_task(self._pump_stderr(self._proc.stderr))
        try:
            await self._ready()
        except BaseException:
            await self._stop(kill=True)  # a process that never got ready is not left running
            raise

    async def _ready(self) -> None:
        try:
            line = await asyncio.wait_for(self._readline(), self.spec.startup_timeout_s)
        except TimeoutError:
            raise TargetError(
                "exception", f'no {{"ready": true}} within {self.spec.startup_timeout_s}s'
            ) from None
        try:
            ready = json.loads(line)
        except ValueError:
            ready = None
        if ready != {"ready": True}:
            raise TargetError("exception", f'first line was not {{"ready": true}}: {line[:200]!r}')

    async def _exits(self, proc: asyncio.subprocess.Process, after: str, then: str) -> bool:
        try:
            await asyncio.wait_for(proc.wait(), self.grace_s)
            return True
        except TimeoutError:
            self._note(f"process still running {self.grace_s}s after {after}: {then}")
            return False

    @staticmethod
    def _signal(proc: asyncio.subprocess.Process, sig: signal.Signals) -> None:
        """Signal the process's whole group (its pid is the group id: start_new_session)."""
        with contextlib.suppress(ProcessLookupError, PermissionError):  # already gone
            os.killpg(proc.pid, sig)

    async def _stop(self, *, kill: bool = False) -> None:
        """Close stdin, then escalate: terminate, kill. `kill`: at once (a restart)."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.stdin is not None:
            proc.stdin.close()
        if kill:
            self._signal(proc, signal.SIGKILL)
        elif not await self._exits(proc, "closed stdin", "terminate"):
            self._signal(proc, signal.SIGTERM)
            if not await self._exits(proc, "SIGTERM", "kill"):
                self._signal(proc, signal.SIGKILL)
        await proc.wait()
        self._signal(proc, signal.SIGKILL)  # whatever it started and left behind
        if self._stderr_task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._stderr_task, max(self.grace_s, 1.0))  # last bytes
            self._stderr_task = None

    async def _readline(self) -> bytes:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            line = await self._proc.stdout.readline()
        except ValueError as exc:  # asyncio's LimitOverrunError, re-raised by readline
            raise TargetError(
                "truncated", f"reply line longer than max_line_bytes ({self.spec.max_line_bytes})"
            ) from exc
        if not line:
            raise _Exited
        return line

    async def setup(self, dataset: "Dataset") -> None:
        del dataset
        await self._start()

    async def teardown(self) -> None:
        await self._stop()

    # -------------------------------------------------------------- cases

    async def _restart_if_needed(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is not None:
            # It exited between cases: an exit like any other, but this case never reached it.
            await self._stop(kill=True)
            self._count_exit(proc.returncode, " between cases")
            if self._abandoned is not None:
                raise TargetError("exception", f"not run: {self._abandoned}")
        if self._proc is not None and not self._in_flight:
            return
        why = "the process exited" if self._proc is None else "a timeout or a reply out of step"
        await self._stop(kill=True)
        self._note(f"restarted after {why}")
        try:
            await self._start()
        except TargetError as exc:  # never became ready: counts as an exit
            raise _Exited from exc

    async def _exchange(self, case: Case, repeat: int) -> _Reply:
        await self._restart_if_needed()
        assert self._proc is not None and self._proc.stdin is not None
        request = {"case": case.model_dump(mode="json"), "repeat": repeat, "config": self.params}
        self._in_flight = True
        try:
            self._proc.stdin.write(json.dumps(request, ensure_ascii=False).encode() + b"\n")
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise _Exited from exc
        line = await self._readline()
        try:
            reply = _Reply.model_validate_json(line)
        except ValidationError as exc:
            raise TargetError(
                "invalid_output",
                f"reply is not a reply line: {exc}",
                value=line[:500].decode("utf-8", errors="replace"),
            ) from exc
        if (reply.case_id, reply.repeat) != (case.id, repeat):
            raise TargetError(
                "invalid_output",
                f"reply for {reply.case_id!r} repeat {reply.repeat}, "
                f"expected {case.id!r} repeat {repeat}",
            )
        self._in_flight = False
        return reply

    def _count_exit(self, code: int | None, when: str = "") -> None:
        """One restart after an exit; the second exit abandons the target."""
        if self._restarted:
            self._abandoned = f"the target process exited twice (last exit code {code})"
            self._note(f"process exited (code {code}){when} a second time: not restarted")
        else:
            self._restarted = True
            self._note(f"process exited (code {code}){when}: restarting once")

    async def _crashed(self) -> TargetError:
        proc = self._proc
        await self._stop(kill=True)
        code = proc.returncode if proc is not None else None  # known once waited for
        self._count_exit(code)
        if self._abandoned is not None:
            return TargetError("exception", f"{self._abandoned}; not restarted")
        return TargetError("exception", f"the target process exited (code {code})")

    async def run(self, case: Case) -> Output:
        repeat = current_repeat.get()
        async with self._lock:
            if self._abandoned is not None:
                raise TargetError("exception", f"not run: {self._abandoned}")
            started = time.perf_counter()
            try:
                reply = await self._exchange(case, repeat)
            except _Exited:
                raise await self._crashed() from None
            latency_ms = (time.perf_counter() - started) * 1000
        if reply.error is not None:
            raise TargetError(reply.error.kind, reply.error.message, latency_ms=latency_ms)
        trace = reply.trace
        if trace.latency_ms is None:
            trace = trace.model_copy(update={"latency_ms": latency_ms, "latency_scope": "command"})
        return Output(case_id=case.id, repeat=repeat, value=reply.output, trace=trace)
