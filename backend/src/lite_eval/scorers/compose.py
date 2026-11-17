"""Composite scorers and building scorers from specs (design §5.5, §18.20).

`all_of`: value = min, passed = all. `any_of`: value = max, passed = any. `weighted`: weighted
mean, passed = None. Each child's own score is kept in the composite's evidence.

Accepted spec forms: `"exact_match"`, `{name: contains, case_sensitive: false}` (the suite form),
`{contains: {case_sensitive: false}}` (the recorded form), `{all_of: [...]}`, `{any_of: [...]}`,
`{weighted: [{scorer: ..., weight: 2}, ...]}`. All of them normalise to one canonical spec, so one
scorer has one id however it was written.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from lite_eval.contracts import Case, Output, Score, Verdict
from lite_eval.errors import ConfigError
from lite_eval.judges.spec import JudgeSpec
from lite_eval.scorers.base import Scorer, Spec, safe_score, spec_name
from lite_eval.scorers.deterministic import deterministic
from lite_eval.scorers.judge import JudgeScorer, judge_scorer
from lite_eval.scorers.judging import judging_scorer
from lite_eval.scorers.memory import memory_scorer
from lite_eval.scorers.ranking import rank_scorer

_COMPOSITES = ("all_of", "any_of", "weighted")


@dataclass(frozen=True)
class Composite:
    op: str  # all_of | any_of | weighted
    children: tuple[Scorer, ...]
    weights: tuple[float, ...] = ()  # weighted only

    @property
    def spec(self) -> Spec:
        if self.op == "weighted":
            return {
                "weighted": [
                    {"scorer": c.spec, "weight": w}
                    for c, w in zip(self.children, self.weights, strict=True)
                ]
            }
        return {self.op: [c.spec for c in self.children]}

    @cached_property
    def name(self) -> str:
        return spec_name(self.spec)

    @property
    def verdict(self) -> Verdict:
        if self.op == "weighted":
            return "none"
        return "value" if all(c.verdict == "value" for c in self.children) else "separate"

    @cached_property
    def version(self) -> str:
        return "1(" + ",".join(c.version for c in self.children) + ")"

    def score(self, case: Case, output: Output) -> Score:
        scores = [safe_score(c, case, output) for c in self.children]
        values = [s.value for s in scores]
        passed = [s.passed is True for s in scores]
        ok: bool | None
        if self.op == "all_of":
            value, ok = min(values), all(passed)
        elif self.op == "any_of":
            value, ok = max(values), any(passed)
        else:
            value = sum(w * v for w, v in zip(self.weights, values, strict=True)) / sum(
                self.weights
            )
            ok = None
        return Score(
            case_id=output.case_id,
            repeat=output.repeat,
            scorer=self.name,
            scorer_version=self.version,
            value=value,
            passed=ok,
            reason=f"{self.op} of {len(scores)}",
            evidence={s.scorer: {"value": s.value, "passed": s.passed} for s in scores},
        )


def _child(spec: Any, judges: Mapping[str, JudgeSpec] | None) -> Scorer:
    scorer = build_scorer(spec, judges)
    if isinstance(scorer, JudgeScorer):  # composites score synchronously (design §18.24)
        raise ConfigError(f"a model judge cannot be part of a composite: {scorer.name}")
    return scorer


def _composite(op: str, items: Any, judges: Mapping[str, JudgeSpec] | None) -> Composite:
    if not isinstance(items, list) or not items:
        raise ConfigError(f"{op} needs a non-empty list")
    if op != "weighted":
        return Composite(op, tuple(_child(i, judges) for i in items))
    try:
        children = tuple(_child(i["scorer"], judges) for i in items)
        weights = tuple(float(i["weight"]) for i in items)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"weighted items must be {{scorer, weight}}: {exc}") from exc
    if any(w <= 0 for w in weights):
        raise ConfigError("weighted: every weight must be > 0")
    return Composite(op, children, weights)


def _leaf(kind: str, params: dict[str, Any], judges: Mapping[str, JudgeSpec] | None) -> Scorer:
    judge = judge_scorer(kind, params, judges)
    if judge is not None:
        return judge
    for family in (rank_scorer, memory_scorer, judging_scorer):
        scorer = family(kind, params)
        if scorer is not None:
            return scorer
    return deterministic(kind, params)


def build_scorer(spec: Any, judges: Mapping[str, JudgeSpec] | None = None) -> Scorer:
    """`judges`: the suite's role -> model map, for `judge` scorers (design §9)."""
    if isinstance(spec, str):
        return _leaf(spec, {}, judges)
    if not isinstance(spec, dict) or not spec:
        raise ConfigError(f"invalid scorer spec {spec!r}")
    if "name" in spec:
        return _leaf(str(spec["name"]), {k: v for k, v in spec.items() if k != "name"}, judges)
    if len(spec) != 1:
        raise ConfigError(f"scorer spec must have `name` or exactly one key: {spec!r}")
    ((key, value),) = spec.items()
    if key in _COMPOSITES:
        return _composite(key, value, judges)
    if not isinstance(value, dict):
        raise ConfigError(f"scorer {key!r}: params must be a mapping, got {value!r}")
    return _leaf(key, value, judges)


def parse_scorer_id(text: str) -> Spec:
    """A spec from text: a bare name, or any accepted spec form as JSON (CLI, run records)."""
    if not text.startswith("{"):
        return text
    try:
        spec = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"scorer {text!r} is not valid JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise ConfigError(f"scorer {text!r} must be a JSON object")
    return spec
