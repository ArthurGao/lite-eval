"""Paired comparison of two runs (design §8.2, §18.1, §18.2, §18.3, §18.21).

Only comparable cases are paired: same dataset name, and per case the same content hash. A metric
is compared only when both runs scored it with the same scorer name and version. A failed case is
a failure (value 0), never missing. A pass / fail metric (a scorer whose value is its verdict, or
a `:pass_rate`) of two single-repeat runs gets McNemar's exact test; everything else a paired
bootstrap seeded from the two run ids and the metric. Which metrics are pass / fail comes from
the scorers' recorded verdicts, never from the values a run happened to produce.
"""

from collections.abc import Sequence
from fractions import Fraction
from math import comb
from statistics import fmean
from typing import Any

import numpy as np

from lite_eval.analysis.summary import PASS_RATE, metric_values
from lite_eval.canonical import sha256_hex
from lite_eval.contracts import (
    CaseChange,
    CaseChangeKind,
    Comparison,
    MetricDelta,
    RunRecord,
    RunWarning,
    Score,
    ScorerRef,
)
from lite_eval.errors import ConfigError

BOOTSTRAP_RESAMPLES = 10_000
ALPHA = 0.05
_CHUNK = 1_000  # resamples drawn at a time: bounds memory at _CHUNK x n indices


def mcnemar_p(b: int, c: int) -> float:
    """Exact two-sided McNemar test without continuity correction (design §18.2):
    p = min(1, 2 * BinomCDF(min(b, c); b + c, 0.5)), and 1 when b + c = 0."""
    n = b + c
    if n == 0:
        return 1.0
    tail = Fraction(sum(comb(n, i) for i in range(min(b, c) + 1)), 2**n)
    return float(min(Fraction(1), 2 * tail))


def bootstrap_seed(baseline_run: str, candidate_run: str, metric: str) -> int:
    digest = sha256_hex(f"{baseline_run}|{candidate_run}|{metric}")
    return int(digest[:16], 16)


def paired_bootstrap(
    baseline: Sequence[float], candidate: Sequence[float], seed: int
) -> tuple[float, float]:
    """95% percentile interval of mean(candidate) - mean(baseline), resampling cases.

    Resampling the per-case differences is the same statistic; drawing in chunks of _CHUNK rows
    reads the generator in the same order as one (B, n) draw, at a fraction of the memory.
    """
    diff = np.asarray(candidate, dtype=float) - np.asarray(baseline, dtype=float)
    rng = np.random.default_rng(seed)
    means = np.empty(BOOTSTRAP_RESAMPLES)
    for start in range(0, BOOTSTRAP_RESAMPLES, _CHUNK):
        rows = min(_CHUNK, BOOTSTRAP_RESAMPLES - start)
        idx = rng.integers(0, len(diff), size=(rows, len(diff)))
        means[start : start + rows] = diff[idx].mean(axis=1)
    low, high = np.percentile(means, [100 * ALPHA / 2, 100 * (1 - ALPHA / 2)])
    return float(low), float(high)


def _per_case(
    record: RunRecord, scores: Sequence[Score], scorer: str, case_ids: list[str]
) -> dict[str, dict[str, float]]:
    """metric -> case id -> mean over repeats (design §18.1)."""
    with_rate = scorer + PASS_RATE in record.summary.metrics
    values = metric_values(scores, scorer, case_ids, record.repeats, pass_rate=with_rate)
    return {m: {cid: fmean(v) for cid, v in per.items()} for m, per in values.items()}


def _overlap(a: RunRecord, b: RunRecord) -> tuple[list[str], list[str], list[str], list[str]]:
    """(compared, changed, added, removed) case ids."""
    if not a.case_hashes or not b.case_hashes:
        raise ConfigError(
            "a run predates per-case hashes (lite-eval E1); regenerate it to compare (§18.3)"
        )
    common = sorted(set(a.case_hashes) & set(b.case_hashes))
    compared = [c for c in common if a.case_hashes[c] == b.case_hashes[c]]
    changed = [c for c in common if a.case_hashes[c] != b.case_hashes[c]]
    added = sorted(set(b.case_hashes) - set(a.case_hashes))
    removed = sorted(set(a.case_hashes) - set(b.case_hashes))
    return compared, changed, added, removed


def _pass_fail(ref: ScorerRef, metric: str, xs: list[float], ys: list[float]) -> bool:
    """Whether a metric is pass / fail: from the recorded verdict; from the values only for a
    record written before verdicts were recorded."""
    if metric.endswith(PASS_RATE):
        return True
    if ref.verdict is not None:
        return ref.verdict == "value"
    return all(v in (0.0, 1.0) for v in xs + ys)


def _within_noise(a: RunRecord, b: RunRecord, metric: str, delta: float) -> bool:
    """Design §18.1: either run's spread over repeats is at least the difference of the means."""
    spreads = [
        r.summary.metrics[metric].max - r.summary.metrics[metric].min
        for r in (a, b)
        if metric in r.summary.metrics
    ]
    return any(s > 0 and s >= abs(delta) for s in spreads)


def _test(
    xs: list[float], ys: list[float], pass_fail: bool, single: bool, seed: int
) -> dict[str, Any]:
    if pass_fail and single:
        worse = sum(x == 1.0 and y == 0.0 for x, y in zip(xs, ys, strict=True))
        better = sum(x == 0.0 and y == 1.0 for x, y in zip(xs, ys, strict=True))
        p = mcnemar_p(worse, better)
        return {"test": "mcnemar", "p_value": p, "significant": p < ALPHA}
    low, high = paired_bootstrap(xs, ys, seed)
    return {
        "test": "bootstrap",
        "ci_low": low,
        "ci_high": high,
        "significant": not (low <= 0.0 <= high),
    }


def _note(d: MetricDelta) -> str:
    detail = f"n={d.n}, {d.discordant} discordant case{'s' if d.discordant != 1 else ''}"
    stat = (
        f"p={d.p_value:.3g}"
        if d.test == "mcnemar"
        else f"95% CI [{d.ci_low:+.4f}, {d.ci_high:+.4f}]"
    )
    verdict = "significant" if d.significant else "not significant"
    return f"{verdict} ({detail}; {d.test} {stat})"


def _case_changes(
    metric: str, ids: list[str], xs: list[float], ys: list[float], pass_fail: bool
) -> list[CaseChange]:
    out = []
    for cid, x, y in zip(ids, xs, ys, strict=True):
        if x != y:
            kind: CaseChangeKind = ("regressed" if y < x else "fixed") if pass_fail else "changed"
            out.append(
                CaseChange(
                    case_id=cid,
                    metric=metric,
                    kind=kind,
                    baseline=x,
                    candidate=y,
                    direction="worse" if y < x else "better",
                )
            )
    return out


def _incomparable(
    scorer: str,
    a: RunRecord,
    b: RunRecord,
    refs_a: dict[str, ScorerRef],
    refs_b: dict[str, ScorerRef],
) -> list[MetricDelta]:
    if scorer not in refs_a or scorer not in refs_b:
        why = "scored by one run only"
    else:
        why = f"scorer version {refs_a[scorer].version} vs {refs_b[scorer].version}"
    return [
        MetricDelta(metric=m, status="incomparable", reason=why)
        for m in (scorer, scorer + PASS_RATE)
        if m in a.summary.metrics or m in b.summary.metrics
    ]


def compare(
    a: RunRecord, a_scores: Sequence[Score], b: RunRecord, b_scores: Sequence[Score]
) -> Comparison:
    """`a` is the baseline, `b` the candidate."""
    if a.dataset.name != b.dataset.name:
        raise ConfigError(
            f"runs use different datasets ({a.dataset.name!r} vs {b.dataset.name!r}); "
            "they cannot be compared (§18.3)"
        )
    compared, changed, added, removed = _overlap(a, b)
    refs_a = {s.name: s for s in a.scorers}
    refs_b = {s.name: s for s in b.scorers}
    single = a.repeats == 1 and b.repeats == 1

    deltas: list[MetricDelta] = []
    changes: list[CaseChange] = []
    for scorer in sorted(refs_a.keys() | refs_b.keys()):
        ref_a, ref_b = refs_a.get(scorer), refs_b.get(scorer)
        if ref_a is None or ref_b is None or ref_a.version != ref_b.version:
            deltas += _incomparable(scorer, a, b, refs_a, refs_b)
            continue
        per_a = _per_case(a, a_scores, scorer, compared)
        per_b = _per_case(b, b_scores, scorer, compared)
        for metric in sorted(per_a.keys() & per_b.keys()):
            xs = [per_a[metric][c] for c in compared]
            ys = [per_b[metric][c] for c in compared]
            if not xs:
                deltas.append(
                    MetricDelta(metric=metric, status="incomparable", reason="no common case")
                )
                continue
            pass_fail = _pass_fail(ref_a, metric, xs, ys)
            base_mean, cand_mean = fmean(xs), fmean(ys)
            seed = bootstrap_seed(a.run_id, b.run_id, metric)
            d = MetricDelta(
                metric=metric,
                status="compared",
                baseline=base_mean,
                candidate=cand_mean,
                delta=cand_mean - base_mean,
                n=len(xs),
                discordant=sum(x != y for x, y in zip(xs, ys, strict=True)),
                within_noise=_within_noise(a, b, metric, cand_mean - base_mean),
                **_test(xs, ys, pass_fail, single, seed),
            )
            deltas.append(d.model_copy(update={"note": _note(d)}))
            changes += _case_changes(metric, compared, xs, ys, pass_fail)

    # Regressions first (design §8.2): every case that got worse, the largest drop first, then
    # every case that got better.
    changes.sort(
        key=lambda c: (c.candidate > c.baseline, c.candidate - c.baseline, c.metric, c.case_id)
    )
    overlap = "full" if not (changed or added or removed) else "partial_overlap"
    warnings = []
    if overlap == "partial_overlap":
        warnings.append(
            RunWarning(
                code="partial_overlap",
                message=f"datasets differ: {len(compared)} cases compared, {len(changed)} changed, "
                f"{len(added)} added, {len(removed)} removed",
            )
        )
    warnings += [
        RunWarning(
            code="within_noise",
            message=f"{d.metric}: a run's spread over repeats is at least the difference",
        )
        for d in deltas
        if d.within_noise
    ]
    return Comparison(
        baseline_run=a.run_id,
        candidate_run=b.run_id,
        baseline_config=a.config_name,
        candidate_config=b.config_name,
        dataset=a.dataset.name,
        overlap=overlap,
        changed_cases=changed,
        added_cases=added,
        removed_cases=removed,
        case_weight=1.0 / len(compared) if compared else 0.0,
        metrics=sorted(deltas, key=lambda d: d.metric),
        changes=changes,
        warnings=warnings,
    )
