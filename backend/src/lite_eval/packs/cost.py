"""The cost pack (design §7, §3 L5/L6): tokens incl. reasoning, cost and latency, with every run."""

import math
import statistics
from collections.abc import Sequence

from lite_eval.contracts import CostSummary, Output


def nearest_rank(values: Sequence[float], q: float) -> float:
    """Design §8.1: the ceil(q * n)-th smallest value; no interpolation."""
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def cost_summary(outputs: Sequence[Output], calls: int) -> CostSummary:
    known = [o.trace.usage for o in outputs if o.trace.usage is not None]
    latencies = [o.trace.latency_ms for o in outputs if o.trace.latency_ms is not None]
    return CostSummary(
        calls=calls,
        input_tokens=sum(u.input_tokens for u in known),
        output_tokens=sum(u.output_tokens for u in known),
        reasoning_tokens=sum(u.reasoning_tokens for u in known),
        cost_usd=sum(u.cost_usd for u in known),
        usage_unknown=len(outputs) - len(known),
        latency_p50_ms=statistics.median(latencies) if latencies else None,
        latency_p95_ms=nearest_rank(latencies, 0.95) if latencies else None,
    )
