"""The two-phase runner (design §6, §18.1, §18.4, §18.5, §18.8, §18.12, §18.13, §18.17, §18.20).

Phase 1 generates outputs (cache, concurrency, timeout, retries, a suite-wide budget) and redacts
every output, cached or new. Phase 2 scores them. Self-checks run across all configs of one
invocation; then each config is written as an immutable run directory. The only module that
calls a target.
"""

import asyncio
import importlib.util
import math
import os
import platform
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lite_eval import __version__
from lite_eval.analysis.gate import gate_problems
from lite_eval.analysis.summary import summarize
from lite_eval.cache import OutputCache, cache_key
from lite_eval.canonical import content_hash
from lite_eval.config import Settings, ci_from_env
from lite_eval.contracts import (
    Billing,
    CacheStats,
    Case,
    CiRecord,
    ErrorInfo,
    ErrorKind,
    Output,
    RedactConfig,
    RunRecord,
    RunStatus,
    RunSummary,
    RunWarning,
    Score,
    ScoreError,
    ScorerRef,
    SuiteRef,
    TargetRecord,
    Trace,
    Usage,
)
from lite_eval.datasets.local import Dataset, load_dataset
from lite_eval.errors import ConfigError, RunError
from lite_eval.judges.client import JudgeCallFailed, JudgeClient, make_client
from lite_eval.judges.spec import JudgeSpec
from lite_eval.packs.cost import cost_summary
from lite_eval.redact import SHORT_SECRET, KnownValues, Redactor, from_spec
from lite_eval.scorers.base import Scorer, failed, safe_score, scorer_raised
from lite_eval.scorers.compose import build_scorer, parse_scorer_id
from lite_eval.scorers.judge import JudgedInvalid, JudgeScorer
from lite_eval.scorers.ranking import RankScorer, ranx_value
from lite_eval.store import baseline_run, load_run, new_run_id, runs_dir, write_run
from lite_eval.suite import Budget, Suite, TargetSpec, load_suite
from lite_eval.targets.base import (
    Target,
    TargetError,
    TransportError,
    current_repeat,
    warming_up,
)
from lite_eval.targets.command import CommandTarget
from lite_eval.targets.http import HttpTarget
from lite_eval.targets.python import build_target, load_factory

Sleep = Callable[[float], Awaitable[None]]

# Warnings that depend on the scores: recomputed by `rescore`, never copied from the original run.
SCORE_DERIVED = frozenset({"all_failed", "identical_scores"})


@dataclass(frozen=True)
class RunOptions:
    no_cache: bool = False
    ci: bool = False
    max_retries: int = 3
    retry_base_s: float = 0.5
    retry_max_s: float = 8.0
    sleep: Sleep = asyncio.sleep
    crosscheck: bool = False  # recompute ranking metrics with ranx (design §18.17)
    # How a judge client is made (tests pass a FakeJudge factory); default: the real clients.
    judge_factory: Callable[[JudgeSpec, float], JudgeClient] | None = None


@dataclass(frozen=True)
class RunResult:
    record: RunRecord
    directory: Path
    scores: list[Score]  # as written to scores.jsonl


def backoff_s(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with the exponent capped at ceil(log2(cap / base)) (design §3 L31)."""
    max_exponent = max(0, math.ceil(math.log2(cap / base)))
    return min(cap, base * 2.0 ** min(attempt, max_exponent))


# ---------------------------------------------------------------- phase 1: generate


@dataclass
class _Ledger:
    """The suite's budget, shared by every config of one invocation (design §6)."""

    budget: Budget
    calls: int = 0
    spent_usd: float = 0.0

    def exhausted(self) -> bool:
        b = self.budget
        if b.max_calls is not None and self.calls >= b.max_calls:
            return True
        return b.max_cost_usd is not None and self.spent_usd > b.max_cost_usd

    def charge(self, usage: Usage | None) -> None:
        if usage is not None:
            self.spent_usd += usage.cost_usd


@dataclass
class _Generation:
    outputs: list[Output] = field(default_factory=list)
    calls: int = 0  # this config's calls, for its cost summary
    skipped: int = 0
    hits: int = 0
    misses: int = 0
    corrupt: int = 0
    warmup_usage: list[Usage] = field(default_factory=list)  # charged, never recorded as output
    abandoned: str | None = None  # the target gave up running cases (design §18.11): partial


def _error_output(
    case: Case,
    kind: ErrorKind,
    message: str,
    *,
    usage: Usage | None = None,
    latency_ms: float | None = None,
    value: Any = None,
) -> Output:
    return Output(
        case_id=case.id,
        repeat=current_repeat.get(),
        value=value,
        trace=Trace(usage=usage, latency_ms=latency_ms),
        error=ErrorInfo(kind=kind, message=message),
    )


@dataclass(frozen=True)
class _Identity:
    """What a config is, read once from the target: the cache key and the record use the same."""

    name: str
    config: dict[str, Any]
    versions: dict[str, str]
    setup: dict[str, Any]
    info: dict[str, str]
    billing: Billing

    @classmethod
    def of(cls, target: Target) -> "_Identity":
        return cls(
            name=target.name,
            config=target.config(),
            versions=target.versions(),
            setup=target.setup_identity(),
            info=target.info(),
            billing=target.billing(),
        )


class _Generator:
    def __init__(
        self,
        suite: Suite,
        dataset: Dataset,
        target: Target,
        identity: _Identity,
        repeats: int,
        redactor: Redactor,
        cache: OutputCache | None,
        ledger: _Ledger,
        options: RunOptions,
    ) -> None:
        self.suite, self.dataset, self.target, self.identity = suite, dataset, target, identity
        self.repeats, self.redactor, self.cache = repeats, redactor, cache
        self.ledger, self.options = ledger, options
        self.state = _Generation()

    def _charge(self, output: Output) -> Output:
        self.ledger.charge(output.trace.usage)
        return output

    async def _call(self, case: Case) -> Output | None:
        timeout = self.suite.spec.timeout_s
        o = self.options
        for attempt in range(o.max_retries + 1):
            if self.ledger.exhausted():
                return None
            self.ledger.calls += 1
            self.state.calls += 1
            started = time.perf_counter()
            try:
                result = await asyncio.wait_for(self.target.run(case), timeout)
            except TransportError as exc:
                if attempt < o.max_retries and not self.ledger.exhausted():
                    await o.sleep(backoff_s(attempt, o.retry_base_s, o.retry_max_s))
                    continue
                why = (
                    f"after {o.max_retries} retries"
                    if attempt == o.max_retries
                    else "and the budget allows no retry"
                )
                return _error_output(case, "exception", f"transport error {why}: {exc}")
            except TimeoutError:
                return _error_output(
                    case, "timeout", f"no reply within {timeout}s", latency_ms=timeout * 1000
                )
            except TargetError as exc:
                measured = (time.perf_counter() - started) * 1000
                return self._charge(
                    _error_output(
                        case,
                        exc.kind,
                        exc.message,
                        usage=exc.usage,
                        latency_ms=exc.latency_ms if exc.latency_ms is not None else measured,
                        value=exc.value,
                    )
                )
            except Exception as exc:
                return _error_output(case, "exception", f"{type(exc).__name__}: {exc}")
            measured = (time.perf_counter() - started) * 1000
            if not isinstance(result, Output):
                return _error_output(
                    case, "invalid_output", f"run() returned {type(result).__name__}"
                )
            trace = result.trace
            if trace.latency_ms is None:
                trace = trace.model_copy(
                    update={"latency_ms": measured, "latency_scope": "wall clock (runner)"}
                )
            return self._charge(
                result.model_copy(
                    update={"case_id": case.id, "repeat": current_repeat.get(), "trace": trace}
                )
            )
        return None  # unreachable: the last attempt returns; kept for the type checker

    def _lookup(self, case: Case, repeat: int) -> tuple[str, Output | None]:
        ident = self.identity
        key = cache_key(
            target=ident.name,
            config=ident.config,
            versions=ident.versions,
            setup=ident.setup,
            dataset_hash=self.dataset.hash,
            case_hash=self.dataset.case_hashes[case.id],
            repeat=repeat,
        )
        if self.cache is None or self.options.no_cache:
            return key, None
        cached, corrupt = self.cache.get(key)
        self.state.corrupt += corrupt
        return key, cached

    async def _warm_up(self, case: Case) -> None:
        """Design §18.18: one untimed call before the timed ones; its output is discarded, its
        call and cost are charged. A failure here is ignored: the real call records it."""
        if self.ledger.exhausted():
            return
        self.ledger.calls += 1
        self.state.calls += 1
        current_repeat.set(0)
        token = warming_up.set(True)
        usage: Usage | None = None
        try:
            result = await asyncio.wait_for(self.target.run(case), self.suite.spec.timeout_s)
            if isinstance(result, Output):
                usage = result.trace.usage
        except TargetError as exc:  # a failure that spent tokens is still charged
            usage = exc.usage
        except Exception:
            pass
        finally:
            warming_up.reset(token)
        if usage is not None:
            self.state.warmup_usage.append(usage)
            self.ledger.charge(usage)

    async def _one(
        self, case: Case, repeat: int, gate: asyncio.Semaphore, key: str, cached: Output | None
    ) -> Output | None:
        current_repeat.set(repeat)  # this task's own context: the target can read it
        if cached is not None:
            self.state.hits += 1
            raw = cached.model_copy(update={"case_id": case.id, "repeat": repeat})
        else:
            async with gate:
                generated = await self._call(case)
            if generated is None:
                self.state.skipped += 1
                return None
            self.state.misses += 1
            raw = generated
        # Redact every output, cached ones too: the suite's rules may have grown since.
        output = self.redactor.output(raw)
        if self.cache is not None and output.error is None and (cached is None or output != raw):
            self.cache.put(key, output)
        return output

    async def run(self) -> _Generation:
        # A serial target (one subprocess) takes one case at a time: cases queued on its lock
        # would otherwise spend their timeout waiting (design §18.11).
        serial = getattr(self.target, "serial", False)
        gate = asyncio.Semaphore(1 if serial else self.suite.spec.concurrency)
        jobs = [
            (c, r, *self._lookup(c, r)) for c in self.dataset.cases for r in range(self.repeats)
        ]
        if any(cached is None for *_, cached in jobs):  # nothing to warm up for a full cache
            await self._warm_up(self.dataset.cases[0])  # cases are sorted by id
        results = await asyncio.gather(*(self._one(c, r, gate, k, hit) for c, r, k, hit in jobs))
        self.state.outputs = sorted(
            (o for o in results if o is not None), key=lambda o: (o.case_id, o.repeat)
        )
        abandoned = getattr(self.target, "abandoned", None)
        self.state.abandoned = abandoned() if callable(abandoned) else None
        return self.state


# ---------------------------------------------------------------- phase 2: score


@dataclass
class _Judging:
    """One config's judge calls (design §18.24): a client per role, the suite's concurrency and
    judge timeout, transport retries, and the budget shared with the targets."""

    clients: Mapping[str, JudgeClient]
    gate: asyncio.Semaphore
    timeout_s: float
    options: RunOptions
    ledger: _Ledger
    calls: int = 0
    skipped: int = 0  # not judged: the budget ran out (the run is partial)

    @property
    def secrets(self) -> dict[str, str]:
        """The judges' keys, masked by value like a target's secrets (design §18.12)."""
        return {k: v for c in self.clients.values() for k, v in c.secrets.items()}

    async def score(self, scorer: JudgeScorer, case: Case, output: Output) -> Score:
        if output.error is not None:
            return failed(scorer, output, f"target error: {output.error.kind}")
        o = self.options
        for attempt in range(o.max_retries + 1):
            # The budget check, the call and its charge share one slot, so a queued call sees
            # what the one before it spent; the backoff happens outside the slot.
            async with self.gate:
                if self.ledger.exhausted():
                    self.skipped += 1
                    error = ScoreError(kind="judge_call_failed", message="budget reached")
                    return failed(scorer, output, "not judged: budget reached", error)
                self.calls += 1
                self.ledger.calls += 1  # a judge call is a model call: `max_calls` covers it
                outcome = await self._attempt(scorer, case, output)
            if not isinstance(outcome, TransportError):
                return outcome
            if attempt < o.max_retries:
                await o.sleep(backoff_s(attempt, o.retry_base_s, o.retry_max_s))
        return self._failed(scorer, output, f"transport error after retries: {outcome}")

    async def _attempt(
        self, scorer: JudgeScorer, case: Case, output: Output
    ) -> Score | TransportError:
        try:
            score = await asyncio.wait_for(
                scorer.ascore(case, output, self.clients[scorer.role]), self.timeout_s
            )
        except TransportError as exc:
            return exc
        except TimeoutError:
            return self._failed(scorer, output, f"no reply within {self.timeout_s}s")
        except JudgeCallFailed as exc:
            return self._failed(scorer, output, str(exc))
        except JudgedInvalid as exc:
            self.ledger.charge(exc.record.usage)
            unusable = failed(scorer, output, "judge reply unusable", exc.error())
            return unusable.model_copy(update={"judge": exc.record})
        except Exception as exc:  # a bug in the scorer
            return scorer_raised(scorer, output, exc)
        self.ledger.charge(score.judge.usage if score.judge else None)
        return score

    @staticmethod
    def _failed(scorer: JudgeScorer, output: Output, message: str) -> Score:
        error = ScoreError(kind="judge_call_failed", message=message)
        return failed(scorer, output, "judge call failed", error)


@asynccontextmanager
async def judging(
    suite: Suite, scorers: Sequence[Scorer], options: RunOptions, ledger: _Ledger
) -> AsyncIterator[_Judging]:
    """A client per role the scorers use (design §18.24), closed on the way out; no clients
    when nothing is judged."""
    make = options.judge_factory or make_client
    roles = {s.role for s in scorers if isinstance(s, JudgeScorer)}
    clients = {role: make(suite.spec.judges[role], suite.spec.judge_timeout_s) for role in roles}
    gate = asyncio.Semaphore(suite.spec.concurrency)
    try:
        yield _Judging(clients, gate, suite.spec.judge_timeout_s, options, ledger)
    finally:
        for client in clients.values():
            await client.aclose()


async def ascore_outputs(
    dataset: Dataset,
    scorers: Sequence[Scorer],
    outputs: Sequence[Output],
    redactor: Redactor,
    judges: _Judging | None = None,
) -> list[Score]:
    """Every scorer on every output, in that order. Deterministic scorers inline; model judges
    concurrently (design §18.24)."""
    cases = {c.id: c for c in dataset.cases}
    scores: list[Score | None] = []
    calls = []
    for output in outputs:
        case = cases[output.case_id]
        for scorer in scorers:
            if isinstance(scorer, JudgeScorer):
                assert judges is not None, "a judge scorer needs the judge runtime"
                calls.append((len(scores), judges.score(scorer, case, output)))
                scores.append(None)
            else:
                scores.append(safe_score(scorer, case, output))
    for (i, _), score in zip(calls, await asyncio.gather(*(c for _, c in calls)), strict=True):
        scores[i] = score
    return [redactor.score(s) for s in scores if s is not None]


# ---------------------------------------------------------------- self-checks (design §18.17)


def _warn(code: str, message: str) -> RunWarning:
    return RunWarning(code=code, message=message)


def _generation_warnings(
    suite: Suite, dataset: Dataset, identity: _Identity, gen: _Generation
) -> list[RunWarning]:
    w = list(dataset.warnings)
    if not identity.versions:
        w.append(
            _warn(
                "unversioned_target",
                "versions() is empty: an engine or model change will not invalidate the cache",
            )
        )
    if dataset.meta.synthetic or any(c.provenance.synthetic for c in dataset.cases):
        w.append(_warn("synthetic_dataset", "the dataset is (partly) synthetic"))
    exercised = {
        s.attrs.get("feature")
        for o in gen.outputs
        for s in o.trace.spans
        if s.kind == "decision" and "feature" in s.attrs
    }
    for feature in suite.spec.expect_features:
        if feature not in exercised:
            w.append(
                _warn(
                    "feature_not_exercised",
                    f"feature {feature!r} appears in no trace: the dataset may not cover it",
                )
            )
    for tag, values in suite.spec.expect_tags.items():
        present = {c.tags.get(tag) for c in dataset.cases}
        w += [
            _warn("empty_group", f"no case has tag {tag}={v}") for v in values if v not in present
        ]
    if gen.corrupt:
        w.append(_warn("cache_corrupt", f"{gen.corrupt} unreadable cache entries regenerated"))
    if gen.skipped:
        w.append(_warn("budget_exhausted", f"budget reached: {gen.skipped} outputs not generated"))
    return w


def _score_warnings(outputs: Sequence[Output], scores: Sequence[Score]) -> list[RunWarning]:
    """Warnings derived from scores (SCORE_DERIVED, except the cross-config identical_scores)."""
    if outputs and not any(s.passed or s.value > 0 for s in scores):
        return [_warn("all_failed", "every case failed")]
    return []


def _identical_score_pairs(results: Sequence["_ConfigResult"]) -> dict[int, list[str]]:
    """Index -> other configs of this invocation with a different config but identical scores."""
    vectors = [
        content_hash(sorted((s.scorer, s.case_id, s.repeat, s.value) for s in r.scores))
        for r in results
    ]
    out: dict[int, list[str]] = {}
    for i, a in enumerate(results):
        for j, b in enumerate(results):
            if i != j and a.scores and a.identity.config != b.identity.config:
                if vectors[i] == vectors[j]:
                    out.setdefault(i, []).append(b.config_name)
    return out


# ---------------------------------------------------------------- orchestration


@dataclass
class _ConfigResult:
    config_name: str
    identity: _Identity
    repeats: int
    started_at: datetime
    finished_at: datetime
    generation: _Generation
    scores: list[Score]
    warnings: list[RunWarning]
    redact: RedactConfig  # the suite's rules, plus the names of the secrets masked by value
    config: dict[str, Any]  # the target's config as recorded: masked (the cache key is not)
    judge_calls: int = 0
    judge_skipped: int = 0  # scores not judged because the budget ran out: the run is partial
    artifacts: dict[str, str] = field(default_factory=dict)  # redacted files for the run dir


@dataclass(frozen=True)
class _Context:
    """Everything shared by the configs of one invocation."""

    suite: Suite
    dataset: Dataset
    scorers: list[Scorer]
    redactor: Redactor
    cache: OutputCache | None
    ledger: _Ledger
    options: RunOptions
    paid_allowed: frozenset[str]
    git_sha: str | None
    git_dirty: bool | None


def _git(cwd: Path) -> tuple[str | None, bool | None]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return sha, bool(status.strip())


def _ci_guard(
    suite: Suite, targets: Sequence[tuple[str, Target]], ci: bool, scorers: Sequence[Scorer]
) -> frozenset[str]:
    """Design §18.13. Returns the config names allowed to run in CI although not free. A model
    judge is a call like a target's: one not declared free is refused the same way (§18.24)."""
    paid = {name: billing for name, t in targets if (billing := t.billing()) != "free"}
    roles = {s.role for s in scorers if isinstance(s, JudgeScorer)}
    judges = {
        f"judge {role}": billing
        for role in sorted(roles)
        if (billing := suite.spec.judges[role].billing) != "free"
    }
    if not ci or not (paid or judges):
        return frozenset()
    if Settings().allow_paid and suite.spec.allow_paid_in_ci:
        return frozenset(paid)
    paid |= judges
    raise ConfigError(
        "CI mode refuses targets not declared free: "
        + ", ".join(f"{n} ({b})" for n, b in sorted(paid.items()))
        + ". Allowing them needs both LITE_EVAL_ALLOW_PAID=1 and `allow_paid_in_ci: true` "
        "in the suite."
    )


def _repeats(suite: Suite, target: Target) -> int:
    if suite.spec.repeats is not None:
        return suite.spec.repeats
    return 1 if getattr(target, "deterministic", True) else 2


def _status(gen: _Generation) -> RunStatus:
    if not gen.outputs:
        return "failed"
    return "partial" if gen.skipped or gen.abandoned else "complete"


def pass_rate_scorers(scorers: Sequence[Scorer]) -> frozenset[str]:
    """Scorers whose pass / fail is not already their value: they get a `:pass_rate` metric."""
    return frozenset(s.name for s in scorers if s.verdict == "separate")


def _scorer_refs(scorers: Sequence[Scorer]) -> list[ScorerRef]:
    return [
        ScorerRef(name=s.name, version=s.version, verdict=s.verdict, calibrated=_calibrated(s))
        for s in scorers
    ]


def _calibrated(scorer: Scorer) -> bool | None:
    """For a model judge: does a calibration run vouch for it (checked before the run)."""
    if not isinstance(scorer, JudgeScorer):
        return None
    return scorer.calibration is not None


def check_calibrations(suite: Suite, scorers: Sequence[Scorer]) -> None:
    """Design §9, §18.24: every judge naming a calibration run is checked against it before any
    call (`judges/calibration.py`); a failure is a ConfigError (exit 2)."""
    for scorer in scorers:
        if isinstance(scorer, JudgeScorer) and scorer.calibration is not None:
            scorer.calibration.check(scorer.name, scorer.identity, suite.root)


def _suite_ref(suite: Suite) -> SuiteRef:
    """The suite path relative to the root (possibly with `..`): never an absolute local path."""
    rel = Path(os.path.relpath(suite.path, suite.root)).as_posix()
    return SuiteRef(name=suite.spec.name, path=rel, sha256=suite.sha256)


def _known(target: Target) -> KnownValues:
    """The secrets a target declares after its setup (`secrets()`, design §18.12), by value."""
    declare = getattr(target, "secrets", None)
    if not callable(declare):
        return KnownValues()
    declared = declare()
    if not isinstance(declared, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in declared.items()
    ):
        raise RunError(f"{target.name}.secrets() must return a dict of str to str")
    return KnownValues(dict(declared))


def _known_or_none(target: Target) -> KnownValues:
    """The target's secrets for masking an error message; none if it cannot say (a failed
    setup may leave `secrets()` unable to answer, and that must not hide the real error)."""
    try:
        return _known(target)
    except Exception:
        return KnownValues()


async def _run_config(
    ctx: _Context, config_name: str, target: Target, extra_warnings: list[RunWarning]
) -> _ConfigResult:
    """Generate and score one config whose setup is already done. Everything it keeps goes
    through the suite's redaction plus the target's own secrets, masked by value."""
    async with judging(ctx.suite, ctx.scorers, ctx.options, ctx.ledger) as judges:
        known = KnownValues({**_known(target).values, **judges.secrets})
        redactor = ctx.redactor.with_known(known)
        identity = _Identity.of(target)
        repeats = _repeats(ctx.suite, target)
        started = datetime.now(UTC)
        generation = await _Generator(
            ctx.suite,
            ctx.dataset,
            target,
            identity,
            repeats,
            redactor,
            ctx.cache,
            ctx.ledger,
            ctx.options,
        ).run()
        scores = await ascore_outputs(
            ctx.dataset, ctx.scorers, generation.outputs, redactor, judges
        )
    collect = getattr(target, "artifacts", None)
    raw_artifacts: dict[str, str] = collect() if callable(collect) else {}
    artifacts = {name: redactor.text(text) for name, text in raw_artifacts.items()}
    warnings = list(extra_warnings)
    for scorer in ctx.scorers:
        if _calibrated(scorer) is False:
            warnings.append(
                _warn(
                    "judge_uncalibrated",
                    f"{scorer.name}: no calibration run vouches for this judge; its scores are "
                    "exploratory and cannot be gated (design §9)",
                )
            )
    if known.short():
        warnings.append(
            _warn(
                "short_secret",
                f"secret(s) {known.short()} shorter than {SHORT_SECRET} characters: masked by "
                "value anyway, which may also hide ordinary text in outputs and scores",
            )
        )
    if generation.abandoned:
        warnings.append(
            _warn("target_abandoned", f"{generation.abandoned}: the cases after it were not run")
        )
    return _ConfigResult(
        config_name=config_name,
        identity=identity,
        repeats=repeats,
        started_at=started,
        finished_at=datetime.now(UTC),
        generation=generation,
        scores=scores,
        warnings=[
            w.model_copy(update={"message": redactor.text(w.message)})
            for w in warnings
            + _generation_warnings(ctx.suite, ctx.dataset, identity, generation)
            + _score_warnings(generation.outputs, scores)
        ],
        redact=redactor.to_config(),
        config=redactor.json(identity.config),
        artifacts=artifacts,
        judge_calls=judges.calls,
        judge_skipped=judges.skipped,
    )


def _crosscheck(
    ctx: _Context, summary: RunSummary, scores: list[Score], repeats: int
) -> list[RunWarning]:
    """Design §18.17: each ranking metric as recorded, against ranx's value."""
    case_ids = [c.id for c in ctx.dataset.cases]
    warnings = []
    for scorer in ctx.scorers:
        if not isinstance(scorer, RankScorer):
            continue
        ours = summary.metrics[scorer.name].value
        theirs = ranx_value(scorer, scores, case_ids, repeats)
        if abs(ours - theirs) > 1e-9:
            warnings.append(
                _warn(
                    "crosscheck_mismatch",
                    f"{scorer.name}: lite-eval {ours:.12f} vs ranx {theirs:.12f}",
                )
            )
    return warnings


def _setup_groups(targets: Sequence[tuple[str, Target]]) -> list[list[tuple[str, Target]]]:
    """Configs that share (target name, setup_identity), in config-name order (design §18.18)."""
    groups: dict[str, list[tuple[str, Target]]] = {}
    for name, target in sorted(targets, key=lambda t: t[0]):
        key = content_hash({"target": target.name, "setup": target.setup_identity()})
        groups.setdefault(key, []).append((name, target))
    return list(groups.values())


async def _run_group(ctx: _Context, group: list[tuple[str, Target]]) -> list[_ConfigResult]:
    """The leader (first) sets up; followers share its setup when they can; teardown runs
    followers first and the leader last, after every config of the group has run."""
    leader_name, leader = group[0]
    ready: list[tuple[str, Target, list[RunWarning]]] = []
    failure: BaseException | None = None
    try:
        for i, (name, target) in enumerate(group):
            share = getattr(target, "share_setup_from", None) if i else None
            notes: list[RunWarning] = []
            try:
                if share is not None:
                    await share(leader)
                else:
                    await target.setup(ctx.dataset)
                    if i:
                        notes.append(
                            _warn(
                                "setup_not_shared",
                                f"same setup_identity as {leader_name!r}, but the target has no "
                                "share_setup_from: set up again",
                            )
                        )
            except Exception as exc:
                known = _known_or_none(target)
                message = known.text(f"setup of config {name!r} failed: {exc}")
                if known.names():  # a traceback of the cause would print it unmasked
                    raise RunError(message) from None
                raise RunError(message) from exc
            ready.append((name, target, notes))
        return [await _run_config(ctx, name, target, notes) for name, target, notes in ready]
    except BaseException as exc:
        failure = exc
        raise
    finally:
        await _teardown_all(ready, failure)


async def _teardown_all(
    ready: list[tuple[str, Target, list[RunWarning]]], failure: BaseException | None
) -> None:
    """Tear every target down, followers first and the leader last; one failing does not stop
    the others. With an error already on its way the teardown failures become notes on it;
    after a clean run the first one is raised as a RunError."""
    problems: list[str] = []
    for name, target, _ in reversed(ready):
        try:
            await target.teardown()
        except Exception as exc:
            known = _known_or_none(target)
            problems.append(known.text(f"teardown of config {name!r} failed: {exc}"))
    if not problems:
        return
    if failure is not None:
        for problem in problems:
            failure.add_note(problem)
        return
    raise RunError("; ".join(problems))


def _record(ctx: _Context, r: _ConfigResult) -> RunRecord:
    gen, ident = r.generation, r.identity
    summary = summarize(
        ctx.dataset.cases,
        r.repeats,
        [s.name for s in ctx.scorers],
        gen.outputs,
        r.scores,
        pass_rate_scorers(ctx.scorers),
    )
    warnings = list(r.warnings)
    if ctx.options.crosscheck:
        warnings += _crosscheck(ctx, summary, r.scores, r.repeats)
    return RunRecord(
        run_id=new_run_id(r.started_at, r.config_name),
        suite=_suite_ref(ctx.suite),
        target=TargetRecord(
            name=ident.name,
            config={"name": r.config_name, **r.config},
            versions=ident.versions,
            info=ident.info,
            billing=ident.billing,
        ),
        dataset=ctx.dataset.ref,
        scorers=_scorer_refs(ctx.scorers),
        repeats=r.repeats,
        seed=ctx.suite.spec.seed,
        concurrency=ctx.suite.spec.concurrency,
        timeout_s=ctx.suite.spec.timeout_s,
        redact=r.redact,
        started_at=r.started_at,
        finished_at=r.finished_at,
        host=platform.node(),
        lite_eval_version=__version__,
        git_sha=ctx.git_sha,
        git_dirty=ctx.git_dirty,
        status="partial" if r.judge_skipped and gen.outputs else _status(gen),
        summary=summary,
        cost=cost_summary(gen.outputs, gen.calls, gen.warmup_usage, r.scores, r.judge_calls),
        warnings=warnings,
        cache=CacheStats(hits=gen.hits, misses=gen.misses),
        ci=CiRecord(ci_mode=ctx.options.ci, paid_allowed=r.config_name in ctx.paid_allowed),
        gate=ctx.suite.spec.gate,
        case_hashes=dict(ctx.dataset.case_hashes),
    )


async def execute(
    suite: Suite,
    dataset: Dataset,
    targets: Sequence[tuple[str, Target]],
    options: RunOptions = RunOptions(),
) -> list[RunResult]:
    if options.crosscheck and importlib.util.find_spec("ranx") is None:
        raise ConfigError("--crosscheck needs ranx: install lite-eval with the [ranking] extra")
    scorers = [build_scorer(s, suite.spec.judges) for s in suite.scorer_specs]
    check_calibrations(suite, scorers)
    paid_allowed = _ci_guard(suite, targets, options.ci, scorers)
    git_sha, git_dirty = _git(suite.path.parent)
    ctx = _Context(
        suite=suite,
        dataset=dataset,
        scorers=scorers,
        redactor=from_spec(suite.spec.redact),
        cache=OutputCache(suite.root / ".lite-eval" / "cache")
        if suite.spec.cache != "off"
        else None,
        ledger=_Ledger(suite.spec.budget),
        options=options,
        paid_allowed=paid_allowed,
        git_sha=git_sha,
        git_dirty=git_dirty,
    )
    # Groups and their configs run one after another, by config name, so the order in the
    # suite cannot change results.
    results = [r for group in _setup_groups(targets) for r in await _run_group(ctx, group)]
    for i, others in _identical_score_pairs(results).items():
        results[i].warnings.append(
            _warn(
                "identical_scores",
                f"identical per-case scores to {sorted(others)} with a different config: "
                "suspect the target ignores the setting, or a bug (design §3 L13)",
            )
        )
    written = []
    for r in results:
        record = _record(ctx, r)
        directory = write_run(
            runs_dir(suite.root), record, r.generation.outputs, r.scores, r.artifacts
        )
        written.append(RunResult(record=record, directory=directory, scores=r.scores))
    return written


def _builder(suite: Suite, spec: TargetSpec) -> Callable[[dict[str, Any]], Target]:
    """How one `targets:` entry turns a config's parameters into a target (design §18.23)."""
    if spec.python is not None:
        factory, where = load_factory(spec.python, suite.import_root), spec.python
        return lambda kwargs: build_target(factory, kwargs, where)
    if spec.http is not None:
        http = spec.http
        return lambda kwargs: HttpTarget(http, kwargs)
    assert spec.command is not None
    command = spec.command
    cwd = (suite.path.parent / command.cwd).resolve() if command.cwd else suite.import_root
    return lambda kwargs: CommandTarget(command, kwargs, cwd)


def prepare_targets(
    suite: Suite, config: str | None = None, announce: Callable[[str], None] | None = None
) -> list[tuple[str, Target]]:
    """Build the targets; `announce` hears each command run and URL called (design §18.11)."""
    targets: list[tuple[str, Target]] = []
    for spec in suite.spec.targets:
        build = _builder(suite, spec)
        for params in spec.configs:
            name = str(params["name"])
            if config is None or name == config:
                target = build({k: v for k, v in params.items() if k != "name"})
                describe = getattr(target, "describe", None)
                if announce is not None and callable(describe):
                    announce(_known_or_none(target).text(f"config {name}: {describe()}"))
                targets.append((name, target))
    if config is not None and not targets:
        raise ConfigError(f"no config named {config!r} in {suite.path}")
    return targets


def precheck_gate(suite: Suite, dataset: Dataset, scorers: Sequence[Scorer]) -> None:
    """Gate configuration errors, found before any target is called (design §18.3, §18.4)."""
    if suite.spec.gate is None:
        raise ConfigError(f"{suite.path}: --gate, but the suite declares no `gate`")
    base = baseline_run(suite.root, suite.spec.name) if suite.spec.gate.relative else None
    gate_problems(
        suite.spec.gate,
        scorers=_scorer_refs(scorers),
        dataset_name=dataset.meta.name,
        case_hashes=dataset.case_hashes,
        baseline=base.record if base else None,
    )


def _announce(line: str) -> None:
    print(f"lite-eval: {line}", file=sys.stderr)


def run_suite(
    path: str | Path,
    *,
    config: str | None = None,
    no_cache: bool = False,
    ci: bool | None = None,
    root: Path | None = None,
    gate: bool = False,
    crosscheck: bool = False,
) -> list[RunResult]:
    """Run every config of a suite (or one) and write one run directory per config.

    With `gate`, the gate's configuration is checked first; evaluate it with `gate_results`.
    """
    suite = load_suite(Path(path), root or Settings().root)
    dataset = load_dataset(suite.dataset_path)
    if gate:
        precheck_gate(
            suite, dataset, [build_scorer(s, suite.spec.judges) for s in suite.scorer_specs]
        )
    targets = prepare_targets(suite, config, announce=_announce)
    options = RunOptions(
        no_cache=no_cache, ci=ci_from_env() if ci is None else ci, crosscheck=crosscheck
    )
    return asyncio.run(execute(suite, dataset, targets, options))


def rescore_run(
    run_dir: str | Path,
    scorer_specs: Sequence[str] | None = None,
    options: RunOptions | None = None,
) -> RunResult:
    """Score a run's stored outputs again; never calls the target. Writes a new run. A model judge
    is called again (re-judging stored outputs is what a changed rubric needs: methodology §7),
    with the suite's `judges:` and budget."""
    stored = load_run(Path(run_dir))
    rec = stored.record
    root = stored.root
    if rec.suite.path is None:
        raise ConfigError("the run does not record its suite path; cannot find the dataset")
    suite = load_suite(root / rec.suite.path, root)
    dataset = load_dataset(suite.dataset_path)
    if dataset.hash != rec.dataset.hash:
        raise ConfigError(
            f"dataset changed since run {rec.run_id} (hash {rec.dataset.hash[:12]} -> "
            f"{dataset.hash[:12]}); generate a new run instead"
        )
    texts = list(scorer_specs) if scorer_specs else [s.name for s in rec.scorers]
    scorers = [build_scorer(parse_scorer_id(t), suite.spec.judges) for t in texts]
    check_calibrations(suite, scorers)
    opts = options or RunOptions(ci=ci_from_env())
    _ci_guard(suite, [], opts.ci, scorers)  # re-judging is a call like judging (§18.13)

    async def rescore() -> tuple[list[Score], int, int]:
        async with judging(suite, scorers, opts, _Ledger(suite.spec.budget)) as judges:
            redactor = Redactor.from_config(rec.redact).with_known(KnownValues(judges.secrets))
            scored = await ascore_outputs(dataset, scorers, stored.outputs, redactor, judges)
            return scored, judges.calls, judges.skipped

    scores, judge_calls, judge_skipped = asyncio.run(rescore())
    now = datetime.now(UTC)
    kept = [w for w in rec.warnings if w.code not in SCORE_DERIVED]
    record = rec.model_copy(
        update={
            "run_id": new_run_id(now, str(rec.target.config.get("name", "rescore"))),
            "scorers": _scorer_refs(scorers),
            "started_at": now,
            "finished_at": datetime.now(UTC),
            "summary": summarize(
                dataset.cases,
                rec.repeats,
                [s.name for s in scorers],
                stored.outputs,
                scores,
                pass_rate_scorers(scorers),
            ),
            "cost": cost_summary(stored.outputs, 0, (), scores, judge_calls),
            "status": "partial" if judge_skipped else rec.status,
            "cache": CacheStats(),
            "warnings": kept + _score_warnings(stored.outputs, scores),
            "rescored_from": rec.run_id,
            "lite_eval_version": __version__,
        }
    )
    directory = write_run(runs_dir(root), record, stored.outputs, scores)
    return RunResult(record=record, directory=directory, scores=scores)
