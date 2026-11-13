"""The two-phase runner (design §6, §18.1, §18.4, §18.5, §18.8, §18.12, §18.13, §18.17, §18.20).

Phase 1 generates outputs (cache, concurrency, timeout, retries, a suite-wide budget) and redacts
every output, cached or new. Phase 2 scores them. Self-checks run across all configs of one
invocation; then each config is written as an immutable run directory. The only module that
calls a target.
"""

import asyncio
import math
import os
import platform
import subprocess
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lite_eval import __version__
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
    RunRecord,
    RunStatus,
    RunWarning,
    Score,
    ScorerRef,
    SuiteRef,
    TargetRecord,
    Trace,
    Usage,
)
from lite_eval.datasets.local import Dataset, load_dataset
from lite_eval.errors import ConfigError, RunError
from lite_eval.packs.cost import cost_summary
from lite_eval.redact import Redactor, from_spec
from lite_eval.scorers.base import Scorer, safe_score
from lite_eval.scorers.compose import build_scorer, parse_scorer_id
from lite_eval.store import load_run, new_run_id, write_run
from lite_eval.suite import Budget, Suite, load_suite
from lite_eval.targets.base import Target, TargetError, TransportError, current_repeat
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


@dataclass(frozen=True)
class RunResult:
    record: RunRecord
    directory: Path


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


@dataclass
class _Generation:
    outputs: list[Output] = field(default_factory=list)
    calls: int = 0  # this config's calls, for its cost summary
    skipped: int = 0
    hits: int = 0
    misses: int = 0
    corrupt: int = 0


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
        if output.trace.usage is not None:
            self.ledger.spent_usd += output.trace.usage.cost_usd
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

    async def _one(self, case: Case, repeat: int, gate: asyncio.Semaphore) -> Output | None:
        current_repeat.set(repeat)  # this task's own context: the target can read it
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
        cached: Output | None = None
        if self.cache is not None and not self.options.no_cache:
            cached, corrupt = self.cache.get(key)
            self.state.corrupt += corrupt
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
        gate = asyncio.Semaphore(self.suite.spec.concurrency)
        jobs = [(c, r) for c in self.dataset.cases for r in range(self.repeats)]
        results = await asyncio.gather(*(self._one(c, r, gate) for c, r in jobs))
        self.state.outputs = sorted(
            (o for o in results if o is not None), key=lambda o: (o.case_id, o.repeat)
        )
        return self.state


# ---------------------------------------------------------------- phase 2: score


def score_outputs(
    dataset: Dataset, scorers: Sequence[Scorer], outputs: Sequence[Output], redactor: Redactor
) -> list[Score]:
    cases = {c.id: c for c in dataset.cases}
    return [
        redactor.score(safe_score(scorer, cases[o.case_id], o))
        for o in outputs
        for scorer in scorers
    ]


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


def _ci_guard(suite: Suite, targets: Sequence[tuple[str, Target]], ci: bool) -> frozenset[str]:
    """Design §18.13. Returns the config names allowed to run in CI although not free."""
    paid = {name: billing for name, t in targets if (billing := t.billing()) != "free"}
    if not ci or not paid:
        return frozenset()
    if Settings().allow_paid and suite.spec.allow_paid_in_ci:
        return frozenset(paid)
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
    return "partial" if gen.skipped else "complete"


def _scorer_refs(scorers: Sequence[Scorer]) -> list[ScorerRef]:
    return [ScorerRef(name=s.name, version=s.version) for s in scorers]


def _suite_ref(suite: Suite) -> SuiteRef:
    """The suite path relative to the root (possibly with `..`): never an absolute local path."""
    rel = Path(os.path.relpath(suite.path, suite.root)).as_posix()
    return SuiteRef(name=suite.spec.name, path=rel, sha256=suite.sha256)


async def _run_config(ctx: _Context, config_name: str, target: Target) -> _ConfigResult:
    identity = _Identity.of(target)
    repeats = _repeats(ctx.suite, target)
    started = datetime.now(UTC)
    try:
        await target.setup(ctx.dataset)
    except Exception as exc:
        raise RunError(f"setup of config {config_name!r} failed: {exc}") from exc
    try:
        generation = await _Generator(
            ctx.suite,
            ctx.dataset,
            target,
            identity,
            repeats,
            ctx.redactor,
            ctx.cache,
            ctx.ledger,
            ctx.options,
        ).run()
    finally:
        await target.teardown()
    scores = score_outputs(ctx.dataset, ctx.scorers, generation.outputs, ctx.redactor)
    return _ConfigResult(
        config_name=config_name,
        identity=identity,
        repeats=repeats,
        started_at=started,
        finished_at=datetime.now(UTC),
        generation=generation,
        scores=scores,
        warnings=_generation_warnings(ctx.suite, ctx.dataset, identity, generation)
        + _score_warnings(generation.outputs, scores),
    )


def _record(ctx: _Context, r: _ConfigResult) -> RunRecord:
    gen, ident = r.generation, r.identity
    return RunRecord(
        run_id=new_run_id(r.started_at, r.config_name),
        suite=_suite_ref(ctx.suite),
        target=TargetRecord(
            name=ident.name,
            config={"name": r.config_name, **ident.config},
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
        redact=ctx.redactor.to_config(),
        started_at=r.started_at,
        finished_at=r.finished_at,
        host=platform.node(),
        lite_eval_version=__version__,
        git_sha=ctx.git_sha,
        git_dirty=ctx.git_dirty,
        status=_status(gen),
        summary=summarize(
            ctx.dataset.cases, r.repeats, [s.name for s in ctx.scorers], gen.outputs, r.scores
        ),
        cost=cost_summary(gen.outputs, gen.calls),
        warnings=r.warnings,
        cache=CacheStats(hits=gen.hits, misses=gen.misses),
        ci=CiRecord(ci_mode=ctx.options.ci, paid_allowed=r.config_name in ctx.paid_allowed),
        gate=ctx.suite.spec.gate,
    )


async def execute(
    suite: Suite,
    dataset: Dataset,
    targets: Sequence[tuple[str, Target]],
    options: RunOptions = RunOptions(),
) -> list[RunResult]:
    paid_allowed = _ci_guard(suite, targets, options.ci)
    git_sha, git_dirty = _git(suite.path.parent)
    ctx = _Context(
        suite=suite,
        dataset=dataset,
        scorers=[build_scorer(s) for s in suite.spec.scorers],
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
    # Configs run one after another, by name, so their order in the suite cannot change results.
    results = [await _run_config(ctx, n, t) for n, t in sorted(targets, key=lambda t: t[0])]
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
        directory = write_run(suite.root / "runs", record, r.generation.outputs, r.scores)
        written.append(RunResult(record=record, directory=directory))
    return written


def prepare_targets(suite: Suite, config: str | None = None) -> list[tuple[str, Target]]:
    targets: list[tuple[str, Target]] = []
    for spec in suite.spec.targets:
        factory = load_factory(spec.python, suite.import_root)
        for params in spec.configs:
            name = str(params["name"])
            if config is None or name == config:
                kwargs = {k: v for k, v in params.items() if k != "name"}
                targets.append((name, build_target(factory, kwargs, spec.python)))
    if config is not None and not targets:
        raise ConfigError(f"no config named {config!r} in {suite.path}")
    return targets


def run_suite(
    path: str | Path,
    *,
    config: str | None = None,
    no_cache: bool = False,
    ci: bool | None = None,
    root: Path | None = None,
) -> list[RunResult]:
    """Run every config of a suite (or one) and write one run directory per config."""
    suite = load_suite(Path(path), root or Settings().root)
    dataset = load_dataset(suite.dataset_path)
    targets = prepare_targets(suite, config)
    options = RunOptions(no_cache=no_cache, ci=ci_from_env() if ci is None else ci)
    return asyncio.run(execute(suite, dataset, targets, options))


def rescore_run(run_dir: str | Path, scorer_specs: Sequence[str] | None = None) -> RunResult:
    """Score a run's stored outputs again; never calls the target. Writes a new run."""
    stored = load_run(Path(run_dir))
    rec = stored.record
    root = stored.directory.parents[2]  # <root>/runs/<suite>/<run_id>
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
    scorers = [build_scorer(parse_scorer_id(t)) for t in texts]
    scores = score_outputs(dataset, scorers, stored.outputs, Redactor.from_config(rec.redact))
    now = datetime.now(UTC)
    kept = [w for w in rec.warnings if w.code not in SCORE_DERIVED]
    record = rec.model_copy(
        update={
            "run_id": new_run_id(now, str(rec.target.config.get("name", "rescore"))),
            "scorers": _scorer_refs(scorers),
            "started_at": now,
            "finished_at": datetime.now(UTC),
            "summary": summarize(
                dataset.cases, rec.repeats, [s.name for s in scorers], stored.outputs, scores
            ),
            "cost": cost_summary(stored.outputs, calls=0),
            "cache": CacheStats(),
            "warnings": kept + _score_warnings(stored.outputs, scores),
            "rescored_from": rec.run_id,
            "lite_eval_version": __version__,
        }
    )
    directory = write_run(root / "runs", record, stored.outputs, scores)
    return RunResult(record=record, directory=directory)
