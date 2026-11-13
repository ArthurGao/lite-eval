"""Run summaries (design §8.1, §18.1, §18.8).

Per scorer and case: the mean over repeats; a missing or failed repeat counts as value 0 and
not passed.
Run-level metric = mean over cases. Spread = the run-level metric computed per repeat (min, max).
The denominator is always cases x repeats.
"""

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

from lite_eval.contracts import Case, MetricSummary, Output, RunRecord, RunSummary, Score

PASS_RATE = ":pass_rate"


def metric_values(
    scores: Sequence[Score],
    scorer: str,
    case_ids: Sequence[str],
    repeats: int,
    pass_rate: bool = False,
) -> dict[str, dict[str, list[float]]]:
    """metric -> case id -> [value per repeat] for one scorer; absent repeats are 0.

    Metrics: the scorer's name (its values) and, when the scorer declares a pass rate,
    `<name>:pass_rate` (a failed or missing repeat counts as not passed). Whether a metric
    exists is the scorer's declaration, never inferred from the data, so one scorer always
    yields the same metrics.
    """
    value = {cid: [0.0] * repeats for cid in case_ids}
    passed = {cid: [0.0] * repeats for cid in case_ids}
    for s in scores:
        if s.scorer != scorer or s.case_id not in value or s.repeat >= repeats:
            continue
        value[s.case_id][s.repeat] = s.value
        passed[s.case_id][s.repeat] = float(s.passed is True)
    out = {scorer: value}
    if pass_rate:
        out[scorer + PASS_RATE] = passed
    return out


@dataclass(frozen=True)
class CaseResult:
    """One case of a run, as the run's summary counts it (§18.1, §18.8)."""

    miss: bool  # an output errored or was never generated, or a scorer failed (§18.9)
    error: str | None  # the first error kind; "not_generated" for a repeat without an output
    scores: dict[str, float]  # scorer -> mean over repeats, a missing repeat counting 0


def case_results(
    record: RunRecord, outputs: Sequence[Output], scores: Sequence[Score]
) -> dict[str, CaseResult]:
    """Every case of the run, in id order, including cases a partial run never reached."""
    ids = sorted(record.case_hashes.keys() | {o.case_id for o in outputs})
    means: dict[str, dict[str, float]] = {cid: {} for cid in ids}
    for ref in record.scorers:
        per_case = metric_values(scores, ref.name, ids, record.repeats)[ref.name]
        for cid, values in per_case.items():
            means[cid][ref.name] = fmean(values)
    failed = {s.case_id for s in scores if s.passed is False or s.error is not None}
    errors: dict[str, str] = {}
    produced: dict[str, set[int]] = defaultdict(set)
    for o in outputs:
        produced[o.case_id].add(o.repeat)
        if o.error is not None:
            errors.setdefault(o.case_id, o.error.kind)
    for cid in ids:
        if len(produced[cid]) < record.repeats:
            errors.setdefault(cid, "not_generated")
    return {
        cid: CaseResult(
            miss=cid in failed or cid in errors, error=errors.get(cid), scores=means[cid]
        )
        for cid in ids
    }


def summarize(
    cases: Sequence[Case],
    repeats: int,
    scorers: Sequence[str],
    outputs: Sequence[Output],
    scores: Sequence[Score],
    pass_rate: frozenset[str] = frozenset(),
) -> RunSummary:
    n = len(cases)
    metrics: dict[str, MetricSummary] = {}
    groups: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for scorer in scorers:
        for metric, per_case in metric_values(
            scores, scorer, [c.id for c in cases], repeats, pass_rate=scorer in pass_rate
        ).items():
            case_mean = {cid: fmean(v) for cid, v in per_case.items()}
            per_repeat = [fmean(per_case[c.id][r] for c in cases) for r in range(repeats)]
            metrics[metric] = MetricSummary(
                value=fmean(case_mean.values()),
                min=min(per_repeat),
                max=max(per_repeat),
                per_repeat=per_repeat,
            )
            by_tag: dict[tuple[str, str], list[float]] = defaultdict(list)
            for c in cases:
                for tag, tag_value in c.tags.items():
                    by_tag[(tag, tag_value)].append(case_mean[c.id])
            for (tag, tag_value), values in by_tag.items():
                groups[tag][tag_value][metric] = fmean(values)
    return RunSummary(
        n_cases=n,
        repeats=repeats,
        case_weight=1.0 / n,
        outputs_expected=n * repeats,
        outputs_recorded=len(outputs),
        metrics=metrics,
        groups={t: dict(v) for t, v in groups.items()},
        target_errors=dict(Counter(o.error.kind for o in outputs if o.error)),
        scoring_errors=dict(Counter(s.error.kind for s in scores if s.error)),
    )
