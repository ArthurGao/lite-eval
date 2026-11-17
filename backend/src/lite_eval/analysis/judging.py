"""A calibration run's confusion matrix (design §9): (expected label, judge's label) -> count,
from the `label_agreement` scores' evidence. Reads contracts only."""

from collections import Counter
from collections.abc import Sequence

from lite_eval.contracts import Score


def confusion(scores: Sequence[Score]) -> dict[tuple[str, str], int]:
    pairs: Counter[tuple[str, str]] = Counter()
    for s in scores:
        if s.scorer == "label_agreement" and isinstance(s.evidence, dict):
            pairs[(str(s.evidence["expected"]), str(s.evidence["got"]))] += 1
    return dict(pairs)
