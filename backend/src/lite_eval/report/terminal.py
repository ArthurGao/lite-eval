"""Plain-text run report for the terminal (design §8.1): numbers, doubt and errors side by side."""

from lite_eval.contracts import RunRecord


def _fmt(x: float | None, digits: int = 3) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def render_run(record: RunRecord, directory: str | None = None) -> str:
    s, c = record.summary, record.cost
    lines = [
        f"run {record.run_id}  [{record.status}]",
        f"  suite {record.suite.name} · config {record.target.config.get('name')} · "
        f"dataset {record.dataset.name}@{record.dataset.version} ({record.dataset.hash[:12]})",
        f"  {s.n_cases} cases x {s.repeats} repeats = {s.outputs_expected} outputs expected, "
        f"{s.outputs_recorded} recorded · 1 case = {s.case_weight:.3f} of a 0-1 metric",
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
    lines.append(f"  cache: {record.cache.hits} hits, {record.cache.misses} generated")
    for w in record.warnings:
        lines.append(f"  WARNING {w.code}: {w.message}")
    if directory:
        lines.append(f"  written to {directory}")
    return "\n".join(lines)
