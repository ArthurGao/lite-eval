"""Gate evaluation (design §8.4, §18.3, §18.4, §18.19, §18.21). Reads contracts only.

`gate_problems` is the one statement of what makes a gate unusable (exit 2): the runner calls it
before any target call with what the suite declares, and the gate calls it again with what the
run records, so the two can never disagree.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from lite_eval.contracts import (
    COST_METRICS,
    CaseChange,
    Comparison,
    GateRule,
    GateSpec,
    RunRecord,
    ScorerRef,
)
from lite_eval.errors import ConfigError

PASS_RATE = ":pass_rate"

# Thresholds hold up to float error: a drop of exactly `max_drop` (0.95 - 1.0 is
# -0.050000000000000044) passes.
EPS = 1e-9


@dataclass(frozen=True)
class GateOutcome:
    run_id: str
    config: str
    failures: list[str] = field(default_factory=list)
    regressions: list[CaseChange] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def gate_metrics(scorers: Sequence[ScorerRef]) -> set[str]:
    """Every name a gate rule may use: scorer values, declared pass rates, cost metrics."""
    names = {s.name for s in scorers}
    names |= {s.name + PASS_RATE for s in scorers if s.verdict == "separate"}
    return names | set(COST_METRICS)


def gate_problems(
    spec: GateSpec,
    *,
    scorers: Sequence[ScorerRef],
    dataset_name: str,
    case_hashes: dict[str, str],
    baseline: RunRecord | None,
) -> None:
    """Raise ConfigError for a gate that cannot be evaluated (design §18.3, §18.4)."""
    known = gate_metrics(scorers)
    for rule in spec.rules:
        if rule.metric not in known:
            raise ConfigError(f"gate metric {rule.metric!r} is not scored; known: {sorted(known)}")
    if not spec.relative:
        return
    if baseline is None:
        raise ConfigError(
            "a relative gate needs a baseline: run the suite, then `lite-eval baseline set <run>`"
        )
    if baseline.dataset.name != dataset_name:
        raise ConfigError(
            f"the baseline uses dataset {baseline.dataset.name!r}, "
            f"this run {dataset_name!r} (§18.3)"
        )
    if baseline.case_hashes != case_hashes and not spec.allow_partial_overlap:
        raise ConfigError(
            "the dataset changed since the baseline (partial_overlap); a relative gate needs "
            "`allow_partial_overlap: true` (§18.3)"
        )
    ours = {s.name: s.version for s in scorers}
    theirs = {s.name: s.version for s in baseline.scorers}
    for rule in spec.relative:
        scorer = rule.metric.removesuffix(PASS_RATE)
        if theirs.get(scorer) != ours.get(scorer):
            raise ConfigError(
                f"gate metric {rule.metric!r} is incomparable with the baseline: scorer "
                f"{scorer!r} version {theirs.get(scorer)} vs {ours.get(scorer)} (§18.3)"
            )


def metric_value(record: RunRecord, metric: str) -> float:
    if metric in COST_METRICS:
        value = getattr(record.cost, COST_METRICS[metric])
        if value is None:
            raise ConfigError(f"gate metric {metric!r} has no value in run {record.run_id}")
        return float(value)
    return record.summary.metrics[metric].value


def _relative(rule: GateRule, comparison: Comparison) -> str | None:
    """A failure message, or None (configuration was checked by gate_problems)."""
    delta = next(d for d in comparison.metrics if d.metric == rule.metric)
    assert delta.delta is not None and rule.max_drop is not None
    if delta.delta >= -rule.max_drop - EPS:
        return None
    if rule.require_significant and not delta.significant:
        return None  # a drop within noise is reported by compare, not failed (§18.19)
    return (
        f"{rule.metric} dropped {-delta.delta:.4f} from the baseline "
        f"({delta.baseline:.4f} -> {delta.candidate:.4f}), more than {rule.max_drop}; {delta.note}"
    )


def evaluate_gate(
    record: RunRecord, baseline: RunRecord | None, comparison: Comparison | None
) -> GateOutcome:
    """`comparison` is baseline -> this run, present when the gate has relative rules."""
    spec = record.gate
    if spec is None:
        raise ConfigError(f"run {record.run_id} records no gate (its suite declares none)")
    gate_problems(
        spec,
        scorers=record.scorers,
        dataset_name=record.dataset.name,
        case_hashes=record.case_hashes,
        baseline=baseline,
    )
    failures: list[str] = []
    if record.status == "partial" and not spec.allow_partial:
        failures.append("the run is partial (budget reached); set gate.allow_partial to accept it")
    for rule in spec.rules:
        if rule.compare_to is None:
            value = metric_value(record, rule.metric)
            note = f" ({rule.baseline_note})" if rule.baseline_note else ""
            if rule.min is not None and value < rule.min - EPS:
                failures.append(f"{rule.metric} = {value:.4f} < min {rule.min}{note}")
            if rule.max is not None and value > rule.max + EPS:
                failures.append(f"{rule.metric} = {value:.4f} > max {rule.max}{note}")
        else:
            assert comparison is not None
            failure = _relative(rule, comparison)
            if failure:
                failures.append(failure)
    regressions = [c for c in comparison.changes if c.kind == "regressed"] if comparison else []
    return GateOutcome(
        run_id=record.run_id,
        config=str(record.target.config.get("name", "")),
        failures=failures,
        regressions=regressions,
    )
