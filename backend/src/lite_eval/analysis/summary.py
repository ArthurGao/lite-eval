"""Run summaries (design §8.1, §18.1, §18.8).

Per scorer and case: the mean over repeats; a missing or failed repeat counts as value 0 and
not passed.
Run-level metric = mean over cases. Spread = the run-level metric computed per repeat (min, max).
The denominator is always cases x repeats.
"""

from collections import Counter, defaultdict
from collections.abc import Sequence
from statistics import fmean

from lite_eval.contracts import Case, MetricSummary, Output, RunSummary, Score

PASS_RATE = ":pass_rate"


def _metric_values(
    scores: Sequence[Score], scorer: str, cases: Sequence[Case], repeats: int
) -> dict[str, dict[str, list[float]]]:
    """metric -> case id -> [value per repeat] for one scorer; absent repeats are 0."""
    value = {c.id: [0.0] * repeats for c in cases}
    passed = {c.id: [0.0] * repeats for c in cases}
    has_pass = False
    for s in scores:
        if s.scorer != scorer or s.case_id not in value or s.repeat >= repeats:
            continue
        value[s.case_id][s.repeat] = s.value
        if s.passed is not None:
            has_pass = True
            passed[s.case_id][s.repeat] = float(s.passed)
    out = {scorer: value}
    if has_pass:
        out[scorer + PASS_RATE] = passed
    return out


def summarize(
    cases: Sequence[Case],
    repeats: int,
    scorers: Sequence[str],
    outputs: Sequence[Output],
    scores: Sequence[Score],
) -> RunSummary:
    n = len(cases)
    metrics: dict[str, MetricSummary] = {}
    groups: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for scorer in scorers:
        for metric, per_case in _metric_values(scores, scorer, cases, repeats).items():
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
