"""Memory pack scorers (design §7, §18.16 lite-mem, §18.23)."""

import random
from typing import Any

import pytest

from lite_eval.contracts import Case, Output
from lite_eval.errors import ConfigError
from lite_eval.packs import get_pack
from lite_eval.scorers.base import safe_score
from lite_eval.scorers.compose import build_scorer


def _legacy_has(found: list[str], keywords: list[str]) -> bool:
    """lite-mem eval/multihop.py `_has` at S10, copied verbatim (design §18.16)."""
    return any(all(k.lower() in m.lower() for k in keywords) for m in found)


def case(hops: list[list[str]], **extra: Any) -> Case:
    return Case(id="c", input={"q": "?"}, expected={"hops": hops, **extra})


def out(value: Any) -> Output:
    return Output(case_id="c", value=value)


TWO_HOPS = [["Ava", "sister"], ["Lena", "Lisbon"]]


def test_a_hop_hits_when_one_memory_holds_all_its_keywords() -> None:
    found = ["ava's SISTER is Lena", "Lena moved to Porto"]
    s1 = build_scorer("hop1").score(case(TWO_HOPS), out(found))
    s2 = build_scorer("hop2").score(case(TWO_HOPS), out(found))
    both = build_scorer("all_hops").score(case(TWO_HOPS), out(found))
    assert (s1.value, s1.passed, s1.evidence) == (1.0, True, {"memory": "ava's SISTER is Lena"})
    assert (s2.value, s2.passed, s2.evidence) == (0.0, False, {"memory": None})
    assert (both.value, both.passed, both.evidence) == (0.0, False, {"hops": [True, False]})


def test_keywords_split_across_two_memories_do_not_count() -> None:
    found = ["Lena lives in a flat", "the flat is in Lisbon"]
    assert build_scorer("hop2").score(case(TWO_HOPS), out(found)).value == 0.0


def test_chinese_keywords_and_all_hops() -> None:
    hops = [["陈", "哥哥"], ["杭州"]]
    found = ["陈的哥哥叫小明", "小明住在杭州"]
    s = build_scorer("all_hops").score(case(hops), out(found))
    assert (s.value, s.passed, s.evidence) == (1.0, True, {"hops": [True, True]})


def test_lower_not_casefold_and_whitespace_kept_like_the_original() -> None:
    # str.lower keeps "ß"; casefold would turn it into "ss" and match. Whitespace is not
    # collapsed: "New  York" (two spaces) does not contain "new york".
    found = ["Straße 5", "New  York"]
    assert build_scorer("hop1").score(case([["strasse"]]), out(found)).value == 0.0
    assert build_scorer("hop1").score(case([["new york"]]), out(found)).value == 0.0


def test_hits_as_dicts_read_their_text_key() -> None:
    hits = [{"id": "m1", "content": "Ava's sister is Lena"}, {"id": "m2", "content": "x"}]
    assert build_scorer("hop1").score(case(TWO_HOPS), out(hits)).value == 1.0
    named = build_scorer({"name": "hop1", "text": "body", "field": "hits"})
    got = named.score(case(TWO_HOPS), out({"hits": [{"body": "Ava sister"}]}))
    assert got.value == 1.0
    assert named.name == '{"hop1":{"field":"hits","text":"body"}}'


def test_a_hop_the_case_does_not_have_is_a_scoring_error() -> None:
    s = safe_score(build_scorer("hop3"), case(TWO_HOPS), out(["Ava sister"]))
    assert s.error is not None and s.error.kind == "scorer_exception"
    assert "hop3" in s.error.message


def test_unknown_params_are_refused() -> None:
    with pytest.raises(ConfigError, match="unknown params"):
        build_scorer({"name": "all_hops", "case_sensitive": True})


def test_verdict_is_value() -> None:
    assert {build_scorer(n).verdict for n in ("hop1", "hop2", "all_hops")} == {"value"}


def test_memory_pack_defaults() -> None:
    assert get_pack("memory").scorers == ("hop1", "hop2", "all_hops")


def test_equivalent_to_the_original_on_random_inputs() -> None:
    """Design §18.16 layer 1: the original `_has` and the scorers agree on every input."""
    rng = random.Random(7)
    words = ["Ava", "ava", "LENA", "lisbon", "陈", "杭州", "a", "", " ", "Straße", "ss", "İ"]
    for _ in range(2000):
        found = [
            " ".join(rng.choice(words) for _ in range(rng.randint(0, 4)))
            for _ in range(rng.randint(0, 5))
        ]
        hops = [
            [rng.choice(words) for _ in range(rng.randint(1, 3))] for _ in range(rng.randint(1, 3))
        ]
        legacy = [_legacy_has(found, hop) for hop in hops]
        for n, want in enumerate(legacy, start=1):
            assert build_scorer(f"hop{n}").score(case(hops), out(found)).passed is want
        assert build_scorer("all_hops").score(case(hops), out(found)).passed is all(legacy)
