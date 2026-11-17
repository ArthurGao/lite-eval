"""Scorer protocol and the wrapper that turns failures into scores (design §5.5, §18.8).

Every scorer has one canonical spec: a bare name when it has no params, `{kind: params}` when it
has, and `{op: [child specs]}` for composites. Its `name` (the id written in run records) is that
spec as canonical JSON, or the bare name; `rescore` rebuilds a scorer from it.
"""

from typing import Any, Protocol

from lite_eval.canonical import canonical_json
from lite_eval.contracts import Case, Output, Score, ScoreError, Verdict

Spec = str | dict[str, Any]


class Scorer(Protocol):
    @property
    def spec(self) -> Spec: ...

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def verdict(self) -> Verdict:
        """How the scorer judges (contracts.Verdict): decides pass rates and the test."""
        ...

    def score(self, case: Case, output: Output) -> Score: ...


def spec_name(spec: Spec) -> str:
    return spec if isinstance(spec, str) else canonical_json(spec)


def failed(scorer: Scorer, output: Output, reason: str, error: ScoreError | None = None) -> Score:
    return Score(
        case_id=output.case_id,
        repeat=output.repeat,
        scorer=scorer.name,
        scorer_version=scorer.version,
        value=0.0,
        passed=False,
        reason=reason,
        error=error,
    )


def safe_score(scorer: Scorer, case: Case, output: Output) -> Score:
    """A target error is a failed case, not a scoring error; a scorer bug is a scoring error."""
    if output.error is not None:
        return failed(scorer, output, f"target error: {output.error.kind}")
    try:
        return scorer.score(case, output)
    except Exception as exc:
        return scorer_raised(scorer, output, exc)


def scorer_raised(scorer: Scorer, output: Output, exc: Exception) -> Score:
    error = ScoreError(kind="scorer_exception", message=f"{type(exc).__name__}: {exc}")
    return failed(scorer, output, "scorer raised", error)
