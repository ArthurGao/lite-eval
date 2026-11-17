"""The extraction and grounded packs (design §7): recall first, misses and duplicates listed;
grounded is rules plus the grounded judge."""

from typing import Any

import pytest

from lite_eval.contracts import Case, Output
from lite_eval.errors import ConfigError
from lite_eval.judges.spec import JudgeSpec
from lite_eval.packs import get_pack
from lite_eval.scorers.compose import build_scorer


def score(spec: Any, expected: Any, value: Any) -> Any:
    return build_scorer(spec).score(
        Case(id="c", input="", expected=expected), Output(case_id="c", value=value)
    )


def test_item_recall_counts_each_expected_item_found_in_any_output_item() -> None:
    expected = {"items": ["Insurance certificate", "two referees", "Site visit on 3 May"]}
    found = ["Provide an insurance  certificate.", "Two referees required", "Pricing schedule"]
    s = score("item_recall", expected, found)
    assert s.value == pytest.approx(2 / 3) and s.passed is False
    assert s.evidence == {"missing": ["Site visit on 3 May"]}
    assert s.reason == "2/3 items"
    assert score("item_recall", expected, {"items": [*found, "site visit on 3 may"]}).passed is True
    # an item given as {text} counts like a string
    assert score("item_recall", {"items": ["a b"]}, [{"text": "x A  B y"}]).value == 1.0


def test_item_duplicates_reports_repeated_items_after_normalising() -> None:
    s = score(
        "item_duplicates", None, ["Two referees", "two  referees", "Insurance", "two referees"]
    )
    assert s.value == pytest.approx(2 / 4) and s.passed is False
    assert s.evidence == {"duplicates": ["two referees"], "count": 2}
    assert score("item_duplicates", None, ["a", "b"]).passed is True


def test_the_packs_defaults() -> None:
    assert get_pack("extraction").scorers == ("item_recall", "item_duplicates")
    grounded = get_pack("grounded").scorers
    assert grounded == (
        {"contains": {"field": "answer", "expected": "must"}},
        {"must_not": {"field": "answer"}},
        {"judge": {"rubric": "grounded"}},
    )
    judges = {"grounded": JudgeSpec(provider="ollama", model="m", base_url="http://x")}
    built = [build_scorer(s, judges) for s in grounded]
    case = Case(id="c", input={"question": "q"}, expected={"must": ["16"], "must_not": ["maybe"]})
    out = Output(case_id="c", value={"answer": "Use 16 grams, maybe", "sources": []})
    assert [built[0].score(case, out).passed, built[1].score(case, out).passed] == [True, False]
    with pytest.raises(ConfigError, match=r"no `judges\.grounded`"):
        build_scorer(grounded[2], {})
