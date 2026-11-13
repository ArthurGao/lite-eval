"""Packs: a task type's defaults (design §7). The cost pack comes with every run, unlisted."""

from dataclasses import dataclass

from lite_eval.errors import ConfigError


@dataclass(frozen=True)
class Pack:
    name: str
    scorers: tuple[str, ...]  # used when a suite lists no scorers
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
}


def get_pack(name: str) -> Pack:
    if name not in PACKS:
        raise ConfigError(
            f"unknown pack {name!r}; available: {sorted(PACKS)} (cost comes with every run)"
        )
    return PACKS[name]
