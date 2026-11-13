from typing import Any

import pytest

from lite_eval.contracts import Case, ErrorInfo, Output
from lite_eval.errors import ConfigError
from lite_eval.scorers.base import safe_score
from lite_eval.scorers.compose import build_scorer, parse_scorer_id


def score(spec: Any, value: Any, expected: Any) -> tuple[float, bool | None, Any]:
    s = safe_score(
        build_scorer(spec),
        Case(id="c", input="", expected=expected),
        Output(case_id="c", value=value),
    )
    assert s.error is None, s.error
    return s.value, s.passed, s.evidence


@pytest.mark.parametrize(
    ("spec", "value", "expected", "result"),
    [
        ("exact_match", "  Wellington ", "Wellington", (1.0, True)),
        ("exact_match", "wellington", "Wellington", (0.0, False)),
        ({"name": "exact_match", "case_sensitive": False}, "wellington", "Wellington", (1.0, True)),
        ("exact_match", {"b": 1, "a": [2]}, {"a": [2], "b": 1}, (1.0, True)),
        ("contains", "The capital is\nWellington.", "capital is Wellington", (1.0, True)),
        ("contains", "Wellington", ["Wellington", "NZ"], (0.0, False)),
        ({"name": "regex", "pattern": r"\b\d{4}\b"}, "in 2024", None, (1.0, True)),
        ("regex", "abc", "^b", (0.0, False)),
        (
            "keywords",
            "Lives in Shanghai, works at Acme",
            [["shanghai"], ["acme", "works"]],
            (1.0, True),
        ),
        ("keywords", "Lives in Shanghai", [["shanghai"], ["acme"]], (0.5, False)),
        ("keywords", "A b c", ["a", "c"], (1.0, True)),
        ("must_not", "your PIN is 482915", {"must_not": ["482915"]}, (0.0, False)),
        ("must_not", "I won't store that", ["482915"], (1.0, True)),
        ("set_recall", ["a", "b"], ["a", "b", "c", "d"], (0.5, False)),
        ("set_precision", ["a", "x"], ["a", "b"], (0.5, False)),
        ("set_recall", ["a", "b"], ["b", "a"], (1.0, True)),
    ],
)
def test_deterministic_scorers(
    spec: Any, value: Any, expected: Any, result: tuple[float, bool]
) -> None:
    assert score(spec, value, expected)[:2] == result


def test_field_and_expected_paths_select_parts() -> None:
    spec = {"name": "exact_match", "field": "answer", "expected": "facts.0"}

    assert score(spec, {"answer": "x", "noise": 1}, {"facts": ["x", "y"]})[:2] == (1.0, True)


def test_contains_reports_what_is_missing() -> None:
    assert score("contains", "Wellington", ["Wellington", "NZ"])[2] == {"missing": ["NZ"]}


def test_composites_combine_and_keep_each_child() -> None:
    children = ["exact_match", {"name": "contains", "case_sensitive": False}]
    value, expected = "WELLINGTON", "Wellington"

    assert score({"all_of": children}, value, expected)[:2] == (0.0, False)
    v, passed, evidence = score({"any_of": children}, value, expected)
    assert (v, passed) == (1.0, True)
    assert evidence == {
        "exact_match": {"value": 0.0, "passed": False},
        '{"contains":{"case_sensitive":false}}': {"value": 1.0, "passed": True},
    }
    weighted = {
        "weighted": [{"scorer": children[0], "weight": 1}, {"scorer": children[1], "weight": 3}]
    }
    assert score(weighted, value, expected)[:2] == (0.75, None)  # (1 * 0 + 3 * 1) / 4


def test_a_target_error_scores_zero_without_calling_the_scorer() -> None:
    out = Output(case_id="c", value="Wellington", error=ErrorInfo(kind="timeout", message="slow"))
    s = safe_score(build_scorer("exact_match"), Case(id="c", input="", expected="Wellington"), out)

    assert (s.value, s.passed, s.reason, s.error) == (0.0, False, "target error: timeout", None)


@pytest.mark.parametrize(
    "spec",
    [
        "exact_match",
        {"name": "contains", "case_sensitive": False, "field": "a"},
        {"all_of": ["exact_match", {"name": "regex", "pattern": "x"}]},
        {"weighted": [{"scorer": "exact_match", "weight": 2}]},
    ],
)
def test_a_recorded_scorer_id_rebuilds_the_same_scorer(spec: Any) -> None:
    scorer = build_scorer(spec)
    rebuilt = build_scorer(parse_scorer_id(scorer.name))

    assert (rebuilt.name, rebuilt.version) == (scorer.name, scorer.version)


@pytest.mark.parametrize(
    "spec",
    [
        "nope",
        {"name": "contains", "colour": "red"},
        {"all_of": []},
        {"weighted": [{"scorer": "exact_match", "weight": 0}]},
        {"weighted": [{"scorer": "exact_match"}]},
        {"best_of": ["exact_match"]},
        {"all_of": ["exact_match"], "any_of": ["exact_match"]},
    ],
)
def test_invalid_scorer_specs_are_config_errors(spec: Any) -> None:
    with pytest.raises(ConfigError):
        build_scorer(spec)


def test_an_invalid_scorer_json_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="not valid JSON"):
        parse_scorer_id("{name: contains}")


def test_one_scorer_written_two_ways_gets_one_id() -> None:
    short = build_scorer({"all_of": ["contains", {"name": "regex", "pattern": "x"}]})
    long = build_scorer({"all_of": [{"name": "contains"}, {"regex": {"pattern": "x"}}]})

    assert short.name == long.name == '{"all_of":["contains",{"regex":{"pattern":"x"}}]}'
