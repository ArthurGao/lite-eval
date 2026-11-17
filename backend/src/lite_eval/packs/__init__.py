"""Packs: a task type's defaults (design §7). The cost pack comes with every run, unlisted."""

from dataclasses import dataclass
from typing import Any

from lite_eval.errors import ConfigError


@dataclass(frozen=True)
class Pack:
    name: str
    scorers: tuple[str | dict[str, Any], ...]  # used when a suite lists no scorers
    description: str


PACKS: dict[str, Pack] = {
    "retrieval": Pack(
        name="retrieval",
        scorers=("hit@1", "hit@3", "hit@5", "mrr@10", "doc_hit@5"),
        description=(
            "ranked hits [{doc, text, id?}] against {doc, evidence} or {relevant: [ids]}; "
            "a relevance-mode suite usually adds ndcg@10 and recall@10"
        ),
    ),
    "memory": Pack(
        name="memory",
        scorers=("hop1", "hop2", "all_hops"),
        description=(
            "recalled memories [str | {content}] against {hops: [[keyword, ...], ...]}; "
            "evidence recall against {relevant: [session or turn ids]} uses recall@k, "
            "forbidden content must_not"
        ),
    ),
    "extraction": Pack(
        name="extraction",
        scorers=("item_recall", "item_duplicates"),
        description=(
            "extracted items [str | {text}] (or {items: [...]}) against {items: [...]}: recall "
            "first, the missed items listed, duplicates counted"
        ),
    ),
    "grounded": Pack(
        name="grounded",
        scorers=(
            {"contains": {"field": "answer", "expected": "must"}},
            {"must_not": {"field": "answer"}},
            {"judge": {"rubric": "grounded"}},
        ),
        description=(
            "an answer {answer, sources: [{text}]} against {must: [...], must_not: [...]}, and "
            "the grounded judge (needs `judges.grounded`; calibrate it first, design §9)"
        ),
    ),
    "judging": Pack(
        name="judging",
        scorers=("label_agreement",),
        description=(
            "a judge calibrated against labels fixed by construction (design §9): input "
            "{question, answer, sources}, expected {label}; tag each case `construction`"
        ),
    ),
}


def get_pack(name: str) -> Pack:
    if name not in PACKS:
        raise ConfigError(
            f"unknown pack {name!r}; available: {sorted(PACKS)} (cost comes with every run)"
        )
    return PACKS[name]
