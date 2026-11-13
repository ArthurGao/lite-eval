"""Retrieval scorers: lite-rag's judge semantics (design §18.16) and ranx agreement (§18.17)."""

import math
from typing import Any

import pytest

from lite_eval.contracts import Case, Output
from lite_eval.errors import ConfigError
from lite_eval.packs import get_pack
from lite_eval.scorers.base import safe_score
from lite_eval.scorers.compose import build_scorer, parse_scorer_id
from lite_eval.scorers.ranking import evidence_ranks, normalise
from tests.helpers import hit


def value(name: str, hits: list[dict[str, Any]], expected: Any) -> float:
    s = safe_score(
        build_scorer(name),
        Case(id="c", input="", expected=expected),
        Output(case_id="c", value=hits),
    )
    assert s.error is None, s.error
    return s.value


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("## Brewing\nUse **1 gram** of coffee", "Brewing Use 1 gram of coffee"),
        ("a\\_b and \\*c\\*", "a_b and *c*"),
        ("__init__  is   spaced\n\nout", "init is spaced out"),
        ("中文的\n句子", "中文的句子"),  # a PDF line break inside a Chinese sentence
        ("中文 and English", "中文 and English"),  # the space next to a Latin word stays
    ],
)
def test_normalise_matches_lite_rag(text: str, expected: str) -> None:
    assert normalise(text) == expected


def test_evidence_ranks_follow_lite_rags_judge() -> None:
    hits = [
        hit("other", "1 gram of coffee"),  # right words, wrong document
        hit("coffee", "the bloom"),  # right document, no quote: the doc rank
        hit("coffee", "Use **1 gram** of\ncoffee to 16"),  # the answer, after normalising
        hit("coffee", "1 gram of coffee"),
    ]

    assert evidence_ranks(hits, "coffee", "1 gram of coffee") == (3, 2)


def test_a_quote_split_across_two_hits_is_not_answered() -> None:
    hits = [hit("d", "the ratio is 1 gram"), hit("d", "of coffee to 16 grams")]

    assert evidence_ranks(hits, "d", "1 gram of coffee") == (None, 1)


EVIDENCE = {"doc": "d", "evidence": "quote"}
AT_2 = [hit("d", "no"), hit("d", "the quote here")]  # doc rank 1, answer rank 2


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("hit@1", 0.0),
        ("hit@5", 1.0),
        ("doc_hit@1", 1.0),
        ("mrr@10", 0.5),
        ("mrr@1", 0.0),  # beyond k
        ("ndcg@10", 1 / math.log2(3)),
        ("recall@5", 1.0),  # one relevant item
    ],
)
def test_evidence_mode_metric_values(name: str, expected: float) -> None:
    assert value(name, AT_2, EVIDENCE) == pytest.approx(expected, abs=1e-12)


def test_no_answer_scores_zero_everywhere() -> None:
    hits = [hit("x", "quote")]

    assert [value(n, hits, EVIDENCE) for n in ("hit@5", "doc_hit@5", "mrr@10")] == [0.0, 0.0, 0.0]


# Relevance mode: ids a, x, b, a (repeat dropped), y -> a at 1, b at 3; relevant {a, b, c}.
RUN = [hit("a"), hit("x"), hit("b"), hit("a"), hit("y")]
REL = {"relevant": ["a", "b", "c"]}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("hit@1", 1.0),
        ("mrr@10", 1.0),
        ("recall@2", 1 / 3),
        ("recall@5", 2 / 3),
        ("ndcg@3", (1 + 1 / math.log2(4)) / (1 + 1 / math.log2(3) + 1 / math.log2(4))),
    ],
)
def test_relevance_mode_metric_values(name: str, expected: float) -> None:
    assert value(name, RUN, REL) == pytest.approx(expected, abs=1e-12)


def test_relevance_mode_prefers_a_hit_id_over_its_doc() -> None:
    hits = [hit("doc1", hid="doc1#2"), hit("doc1", hid="doc1#0")]

    assert value("mrr@10", hits, {"relevant": ["doc1#0"]}) == 0.5


def test_binary_metrics_pass_and_continuous_ones_do_not() -> None:
    case = Case(id="c", input="", expected=EVIDENCE)
    out = Output(case_id="c", value=AT_2)

    assert safe_score(build_scorer("hit@5"), case, out).passed is True
    assert safe_score(build_scorer("mrr@10"), case, out).passed is None
    assert safe_score(build_scorer("hit@5"), case, out).evidence == {
        "answer_rank": 2,
        "doc_rank": 1,
    }


def test_a_field_path_selects_the_hit_list_and_ids_round_trip() -> None:
    scorer = build_scorer({"name": "hit@5", "field": "hits"})
    case = Case(id="c", input="", expected=EVIDENCE)

    assert safe_score(scorer, case, Output(case_id="c", value={"hits": AT_2})).value == 1.0
    assert (
        build_scorer(parse_scorer_id(scorer.name)).name
        == scorer.name
        == '{"hit@5":{"field":"hits"}}'
    )


@pytest.mark.parametrize("output", ["just text", [1, 2], {"hits": []}])
def test_a_non_hit_list_output_is_a_scoring_error(output: Any) -> None:
    s = safe_score(
        build_scorer("hit@5"),
        Case(id="c", input="", expected=EVIDENCE),
        Output(case_id="c", value=output),
    )

    assert s.error is not None and s.value == 0.0


@pytest.mark.parametrize("spec", ["hit@0", "hits@5", {"name": "mrr@10", "case_sensitive": True}])
def test_invalid_rank_scorers_are_config_errors(spec: Any) -> None:
    with pytest.raises(ConfigError):
        build_scorer(spec)


def test_the_retrieval_pack_defaults() -> None:
    assert get_pack("retrieval").scorers == ("hit@1", "hit@3", "hit@5", "mrr@10", "doc_hit@5")


# ---------------------------------------------------------------- ranx cross-check (design §18.17)

# Fixed runs: per query, the ranked ids; qrels mark relevant ids. Built to hit edge cases: relevant
# at rank 1, beyond k, several relevant, none retrieved, more relevant than k.
RUNS = {
    "q1": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"],
    "q2": ["x", "y", "a", "z", "b", "w", "v", "u", "t", "s"],
    "q3": ["m", "n", "o", "p", "q", "r", "s", "t", "u", "v"],
    "q4": ["k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8", "k9", "r1"],
    "q5": ["r1", "r2", "r3", "r4", "r5", "r6", "r7", "x1", "x2", "x3"],
}
QRELS = {
    "q1": ["a"],
    "q2": ["a", "b", "zz"],
    "q3": ["none"],
    "q4": ["r1"],
    "q5": ["r1", "r2", "r3", "r4", "r5", "r6", "r7"],
}
METRICS = [(m, k) for m in ("hit", "mrr", "ndcg", "recall") for k in (1, 3, 5, 10)]


def test_own_ranking_metrics_equal_ranx() -> None:
    from ranx import Qrels, Run, evaluate

    ranx_name = {"hit": "hit_rate", "mrr": "mrr", "ndcg": "ndcg", "recall": "recall"}
    theirs = evaluate(
        Qrels.from_dict({q: {d: 1 for d in rel} for q, rel in QRELS.items()}),
        Run.from_dict({q: {d: 1.0 / r for r, d in enumerate(ids, 1)} for q, ids in RUNS.items()}),
        [f"{ranx_name[m]}@{k}" for m, k in METRICS],
    )
    for m, k in METRICS:
        ours = sum(
            value(f"{m}@{k}", [hit(d) for d in RUNS[q]], {"relevant": QRELS[q]}) for q in RUNS
        )
        assert ours / len(RUNS) == pytest.approx(theirs[f"{ranx_name[m]}@{k}"], abs=1e-9), (
            f"{m}@{k}"
        )
