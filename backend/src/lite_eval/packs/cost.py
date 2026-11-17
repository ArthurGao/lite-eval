"""The cost pack (design §7, §3 L5/L6): tokens incl. reasoning, cost and latency, with every run."""

import math
import statistics
from collections.abc import Sequence

from lite_eval.contracts import CostSummary, Output, Score, Usage


def nearest_rank(values: Sequence[float], q: float) -> float:
    """Design §8.1: the ceil(q * n)-th smallest value; no interpolation."""
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def cost_summary(
    outputs: Sequence[Output],
    calls: int,
    extra: Sequence[Usage] = (),
    scores: Sequence[Score] = (),
    judge_calls: int = 0,
) -> CostSummary:
    """Totals over the outputs, plus `extra` usage that produced no output (warm-up calls); the
    judges' usage, from the scores' judge records, is summed apart (design §18.24)."""
    recorded = [o.trace.usage for o in outputs if o.trace.usage is not None]
    known = [*recorded, *extra]
    latencies = [o.trace.latency_ms for o in outputs if o.trace.latency_ms is not None]
    judged = [s.judge.usage for s in scores if s.judge is not None and s.judge.usage is not None]
    return CostSummary(
        calls=calls,
        input_tokens=sum(u.input_tokens for u in known),
        output_tokens=sum(u.output_tokens for u in known),
        reasoning_tokens=sum(u.reasoning_tokens for u in known),
        cost_usd=sum(u.cost_usd for u in known),
        usage_unknown=len(outputs) - len(recorded),
        latency_p50_ms=statistics.median(latencies) if latencies else None,
        latency_p95_ms=nearest_rank(latencies, 0.95) if latencies else None,
        judge_calls=judge_calls,
        judge_input_tokens=sum(u.input_tokens for u in judged),
        judge_output_tokens=sum(u.output_tokens for u in judged),
        judge_reasoning_tokens=sum(u.reasoning_tokens for u in judged),
        judge_cost_usd=sum(u.cost_usd for u in judged),
    )
