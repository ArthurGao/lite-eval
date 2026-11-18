"""Plain-text run report for the terminal (design §8.1): numbers, doubt and errors side by side."""

from collections import Counter
from collections.abc import Sequence

from lite_eval.analysis.gate import GateOutcome
from lite_eval.analysis.judging import confusion
from lite_eval.contracts import Comparison, RunRecord, Score


def _fmt(x: float | None, digits: int = 3) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def render_run(
    record: RunRecord, directory: str | None = None, scores: Sequence[Score] = ()
) -> str:
    """`scores`: for a calibration run, the agreement matrix is read from them (design §9)."""
    s, c = record.summary, record.cost
    lines = [
        f"run {record.run_id}  [{record.status}]",
        f"  suite {record.suite.name} · config {record.target.config.get('name')} · "
        f"dataset {record.dataset.name}@{record.dataset.version} ({record.dataset.hash[:12]})",
        f"  {s.n_cases} cases x {s.repeats} repeats = {s.outputs_expected} outputs expected, "
        f"{s.outputs_recorded} recorded"
        + (f", {s.errored_outputs} errors" if s.errored_outputs else "")
        + f" · 1 case = {s.case_weight:.3f} of a 0-1 metric",
    ]
    if record.rescored_from:
        lines.append(f"  rescored from {record.rescored_from}")
    if s.metrics:
        width = max(len(m) for m in s.metrics)
        lines.append(f"  {'metric'.ljust(width)}  value  min    max")
        for name, m in s.metrics.items():
            lines.append(f"  {name.ljust(width)}  {_fmt(m.value)}  {_fmt(m.min)}  {_fmt(m.max)}")
    for tag, values in s.groups.items():
        for value, metrics in sorted(values.items()):
            shown = ", ".join(f"{k}={_fmt(v)}" for k, v in metrics.items())
            lines.append(f"  [{tag}={value}] {shown}")
    if s.target_errors or s.scoring_errors:
        errs = ", ".join(f"{k}: {v}" for k, v in sorted(s.target_errors.items()))
        serrs = ", ".join(f"{k}: {v}" for k, v in sorted(s.scoring_errors.items()))
        lines.append(f"  target errors: {errs or 'none'} · scoring errors: {serrs or 'none'}")
    lines.append(
        f"  cost: {c.calls} calls, tokens in {c.input_tokens} / out {c.output_tokens} "
        f"(reasoning {c.reasoning_tokens}), ${c.cost_usd:.4f}"
        + (f", usage unknown for {c.usage_unknown}" if c.usage_unknown else "")
        + f" · latency p50 {_fmt(c.latency_p50_ms, 1)} ms, p95 {_fmt(c.latency_p95_ms, 1)} ms"
    )
    if c.judge_calls:
        lines.append(
            f"  judges: {c.judge_calls} calls, tokens in {c.judge_input_tokens} / out "
            f"{c.judge_output_tokens} (reasoning {c.judge_reasoning_tokens}), "
            f"${c.judge_cost_usd:.4f}"
        )
    matrix = confusion(scores)
    if matrix:
        lines.append("  agreement matrix (expected -> judged):")
        for (expected, got), n in sorted(matrix.items()):
            lines.append(f"    {expected} -> {got}: {n}")
    lines.append(f"  cache: {record.cache.hits} hits, {record.cache.misses} generated")
    for w in record.warnings:
        lines.append(f"  WARNING {w.code}: {w.message}")
    if directory:
        lines.append(f"  written to {directory}")
    return "\n".join(lines)


def render_comparison(c: Comparison, limit: int = 20) -> str:
    """Deltas with their doubt in words; per-case changes, regressions first (design §8.2)."""
    lines = [
        f"compare {c.baseline_run} -> {c.candidate_run}  (dataset {c.dataset}, {c.overlap})",
        f"  1 compared case = {c.case_weight:.3f} of a 0-1 metric",
    ]
    width = max((len(d.metric) for d in c.metrics), default=6)
    for d in c.metrics:
        if d.status == "incomparable":
            lines.append(f"  {d.metric.ljust(width)}  incomparable: {d.reason}")
            continue
        lines.append(
            f"  {d.metric.ljust(width)}  {_fmt(d.baseline)} -> {_fmt(d.candidate)}  "
            f"({(d.delta or 0):+.4f})  {d.note}"
        )
    if c.changes:
        kinds = Counter(x.kind for x in c.changes)
        lines.append(
            f"  cases: {kinds['regressed']} regressed, {kinds['fixed']} fixed, "
            f"{kinds['changed']} changed (worse first)"
        )
        for x in c.changes[:limit]:
            lines.append(
                f"    {x.kind:9} {x.case_id}  {x.metric}  {x.baseline:g} -> {x.candidate:g}"
            )
        if len(c.changes) > limit:
            lines.append(f"    … {len(c.changes) - limit} more")
    for w in c.warnings:
        lines.append(f"  WARNING {w.code}: {w.message}")
    return "\n".join(lines)


def render_gate(outcome: GateOutcome) -> str:
    if outcome.passed and not outcome.applied:  # never read as "checked and fine"
        return f"gate: no gate rule applies to config {outcome.config} ({outcome.run_id})"
    if outcome.passed:
        return f"gate passed: {outcome.config} ({outcome.run_id})"
    lines = [f"gate FAILED: {outcome.config} ({outcome.run_id})"]
    lines += [f"  - {f}" for f in outcome.failures]
    if outcome.regressions:
        ids = sorted({c.case_id for c in outcome.regressions})
        lines.append(f"  regressed cases ({len(ids)}): " + ", ".join(ids))
    return "\n".join(lines)
