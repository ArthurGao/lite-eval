"""Deterministic scorers (design §5.5): a part of the output against a part of `expected`.

Common params: `field` (dotted path into `output.value`), `expected` (dotted path into
`case.expected`); both default to the whole value.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from lite_eval.canonical import canonical_json
from lite_eval.contracts import Case, Output, Score, Verdict
from lite_eval.errors import ConfigError
from lite_eval.scorers.base import Spec, spec_name


def get_path(value: Any, path: str | None) -> Any:
    if not path:
        return value
    for part in path.split("."):
        if isinstance(value, dict):
            if part not in value:
                raise KeyError(f"no key {part!r} in path {path!r}")
            value = value[part]
        elif isinstance(value, list) and part.isdigit():
            value = value[int(part)]
        else:
            raise KeyError(f"cannot follow {part!r} in path {path!r}")
    return value


def _text(value: Any) -> str:
    return value if isinstance(value, str) else canonical_json(value)


def _norm(text: str, case_sensitive: bool) -> str:
    text = " ".join(text.split())
    return text if case_sensitive else text.casefold()


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


Judge = Callable[[Any, Any, dict[str, Any]], tuple[float, bool | None, str, Any]]


@dataclass(frozen=True)
class _Deterministic:
    kind: str
    params: dict[str, Any]
    judge: Judge
    version: str = "1"

    @property
    def spec(self) -> Spec:
        return self.kind if not self.params else {self.kind: self.params}

    @cached_property
    def name(self) -> str:
        return spec_name(self.spec)

    @property
    def verdict(self) -> Verdict:
        return "value" if self.kind in _BINARY_KINDS else "separate"

    def score(self, case: Case, output: Output) -> Score:
        got = get_path(output.value, self.params.get("field"))
        want = get_path(case.expected, self.params.get("expected"))
        value, passed, reason, evidence = self.judge(got, want, self.params)
        return Score(
            case_id=output.case_id,
            repeat=output.repeat,
            scorer=self.name,
            scorer_version=self.version,
            value=value,
            passed=passed,
            reason=reason,
            evidence=evidence,
        )


def _exact(got: Any, want: Any, p: dict[str, Any]) -> tuple[float, bool | None, str, Any]:
    cs = p.get("case_sensitive", True)
    if isinstance(got, str) and isinstance(want, str):
        ok = _norm(got, cs) == _norm(want, cs)
    else:
        ok = canonical_json(got) == canonical_json(want)
    return float(ok), ok, "equal" if ok else "not equal", None


def _contains(got: Any, want: Any, p: dict[str, Any]) -> tuple[float, bool | None, str, Any]:
    cs = p.get("case_sensitive", True)
    text = _norm(_text(got), cs)
    missing = [w for w in _as_list(want) if _norm(_text(w), cs) not in text]
    ok = not missing
    return float(ok), ok, "all present" if ok else f"missing {len(missing)}", {"missing": missing}


def _regex(got: Any, want: Any, p: dict[str, Any]) -> tuple[float, bool | None, str, Any]:
    pattern = p.get("pattern", want)
    if not isinstance(pattern, str):
        raise ValueError("regex scorer needs a `pattern` param or a string expected value")
    match = re.search(pattern, _text(got), 0 if p.get("case_sensitive", True) else re.IGNORECASE)
    ok = match is not None
    return float(ok), ok, "matched" if ok else "no match", match.group(0) if match else None


def _keywords(got: Any, want: Any, p: dict[str, Any]) -> tuple[float, bool | None, str, Any]:
    """`want` is a list of groups (each a list of keywords, all required) or one flat group."""
    groups = want if want and all(isinstance(g, list) for g in want) else [want]
    text = _norm(_text(got), p.get("case_sensitive", False))
    hit = [all(_norm(str(k), p.get("case_sensitive", False)) in text for k in g) for g in groups]
    value = sum(hit) / len(hit)
    return value, all(hit), f"{sum(hit)}/{len(hit)} groups", {"groups_hit": hit}


def _must_not(got: Any, want: Any, p: dict[str, Any]) -> tuple[float, bool | None, str, Any]:
    forbidden = want.get("must_not", []) if isinstance(want, dict) else _as_list(want)
    cs = p.get("case_sensitive", False)
    text = _norm(_text(got), cs)
    found = [f for f in forbidden if _norm(str(f), cs) in text]
    ok = not found
    return (
        float(ok),
        ok,
        "none present" if ok else f"{len(found)} forbidden present",
        {"found": found},
    )


def _set_ratio(of_expected: bool) -> Judge:
    def judge(got: Any, want: Any, p: dict[str, Any]) -> tuple[float, bool | None, str, Any]:
        got_set = {canonical_json(x) for x in _as_list(got)} if got is not None else set()
        want_set = {canonical_json(x) for x in _as_list(want)}
        denom = want_set if of_expected else got_set
        value = len(got_set & want_set) / len(denom) if denom else 1.0
        return value, value == 1.0, f"{len(got_set & want_set)}/{len(denom)}", None

    return judge


def _items(value: Any) -> list[str]:
    """An extraction's items: a list (or `{items: [...]}`) of strings or `{text}` dicts."""
    if isinstance(value, dict) and "items" in value:
        value = value["items"]
    return [
        str(v.get("text", "")) if isinstance(v, dict) else _text(v) for v in _as_list(value or [])
    ]


def _item_recall(got: Any, want: Any, p: dict[str, Any]) -> tuple[float, bool | None, str, Any]:
    """Extraction pack (design §7): each expected item found in some output item, normalised
    as `contains` does (whitespace collapsed, case ignored unless asked); recall first."""
    cs = p.get("case_sensitive", False)
    expected = want.get("items", []) if isinstance(want, dict) else _as_list(want)
    found = [_norm(t, cs) for t in _items(got)]
    missing = [w for w in expected if not any(_norm(_text(w), cs) in f for f in found)]
    n = len(expected)
    value = (n - len(missing)) / n if n else 1.0
    return value, not missing, f"{n - len(missing)}/{n} items", {"missing": missing}


def _item_duplicates(got: Any, want: Any, p: dict[str, Any]) -> tuple[float, bool | None, str, Any]:
    """The share of output items that repeat an earlier one (lower is better)."""
    del want
    items = [_norm(t, p.get("case_sensitive", False)) for t in _items(got)]
    seen: set[str] = set()
    repeats: list[str] = []
    for item in items:
        if item in seen:
            repeats.append(item)
        seen.add(item)
    value = len(repeats) / len(items) if items else 0.0
    evidence = {"duplicates": sorted(set(repeats)), "count": len(repeats)}
    return value, not repeats, f"{len(repeats)} duplicate(s)", evidence


_JUDGES: dict[str, Judge] = {
    "exact_match": _exact,
    "contains": _contains,
    "regex": _regex,
    "keywords": _keywords,
    "must_not": _must_not,
    "set_recall": _set_ratio(of_expected=True),
    "set_precision": _set_ratio(of_expected=False),
    "item_recall": _item_recall,
    "item_duplicates": _item_duplicates,
}
_COMMON_PARAMS = {"field", "expected", "case_sensitive"}
_BINARY_KINDS = {"exact_match", "contains", "regex", "must_not"}
_EXTRA_PARAMS = {"regex": {"pattern"}}


def deterministic(kind: str, params: dict[str, Any]) -> _Deterministic:
    if kind not in _JUDGES:
        raise ConfigError(
            f"unknown scorer {kind!r}; known: {sorted(_JUDGES)} and all_of/any_of/weighted"
        )
    unknown = set(params) - _COMMON_PARAMS - _EXTRA_PARAMS.get(kind, set())
    if unknown:
        raise ConfigError(f"scorer {kind!r}: unknown params {sorted(unknown)}")
    return _Deterministic(kind=kind, params=dict(params), judge=_JUDGES[kind])
