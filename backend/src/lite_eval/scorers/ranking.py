"""Retrieval scorers (design §7 retrieval pack, §18.16 lite-rag definitions).

The output value (or `field` inside it) is the ranked hit list, best first: `[{doc, text, id?}]`.
`expected` (or `expected` inside it) takes one of two forms:

- evidence mode `{doc, evidence}` (lite-rag): scanning from rank 1, the first hit from `doc` is the
  `doc_rank`; the first hit from `doc` whose normalised text contains the normalised evidence quote
  is the `answer_rank`. A quote split across two hits does not count: that is a real weakness of
  the chunker, not of the label (design §3 L8).
- relevance mode `{relevant: [ids]}`: a hit's id is `id`, else `doc`; ranks are over the hit list
  with repeated ids removed (first occurrence kept), as an IR run (ranx) sees it.

Scorer names are `<metric>@<k>`: `hit`, `doc_hit`, `mrr`, `ndcg`, `recall`. Each case scores the
metric's value on that case, so a run-level metric is the plain mean (hit@5, MRR@10 = Σ 1/rank / n).
"""

import math
import re
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from typing import Any

from lite_eval.contracts import Case, Output, Score, Verdict
from lite_eval.errors import ConfigError
from lite_eval.scorers.base import Spec, spec_name
from lite_eval.scorers.deterministic import get_path

NAME = re.compile(r"^(hit|doc_hit|mrr|ndcg|recall)@([1-9][0-9]*)$")
_BINARY = {"hit", "doc_hit"}

# lite-rag `eval._norm`, copied character for character (design §18.16): Markdown headings, `**`,
# `__` and escapes removed, whitespace collapsed, none left between CJK characters.
_MARKUP = re.compile(r"^#{1,6}[ \t]+|\*\*|__|\\(?=[_*#\\])", re.MULTILINE)
_CJK_GAP = re.compile(r"(?<=[　-〿一-鿿＀-￯])\s+(?=[　-〿一-鿿＀-￯])")


@lru_cache(maxsize=65_536)  # every rank scorer normalises the same hit texts
def normalise(text: str) -> str:
    return _CJK_GAP.sub("", " ".join(_MARKUP.sub("", text).split()))


def evidence_ranks(
    hits: list[dict[str, Any]], doc: str, evidence: str
) -> tuple[int | None, int | None]:
    """(answer_rank, doc_rank), 1-based; None when absent (lite-rag `eval.judge`)."""
    quote = normalise(evidence)
    answer = doc_rank = None
    for rank, hit in enumerate(hits, start=1):
        if hit.get("doc") == doc:
            doc_rank = doc_rank or rank
            if quote in normalise(str(hit.get("text", ""))):
                answer = rank
                break
    return answer, doc_rank


def _relevance_ranks(hits: list[dict[str, Any]], relevant: set[str]) -> list[int]:
    """1-based ranks of relevant ids, over the hit list with repeated ids removed."""
    seen: set[str] = set()
    ranks = []
    for hit in hits:
        hid = str(hit.get("id", hit.get("doc")))
        if hid in seen:
            continue
        seen.add(hid)
        if hid in relevant:
            ranks.append(len(seen))
    return ranks


def _dcg(ranks: list[int], k: int) -> float:
    return sum(1 / math.log2(r + 1) for r in ranks if r <= k)


def ranks_of(evidence: dict[str, Any], metric: str) -> tuple[list[int], int]:
    """(ranks of the relevant items, how many there are) from a score's evidence. Evidence mode
    is relevance mode with one relevant item: the answer (or, for doc_hit, the document)."""
    if "relevant_ranks" in evidence:
        return [int(r) for r in evidence["relevant_ranks"]], int(evidence["relevant"])
    rank = evidence.get("doc_rank" if metric == "doc_hit" else "answer_rank")
    return ([int(rank)] if rank else []), 1


def metric_at_k(metric: str, k: int, ranks: list[int], n_relevant: int) -> float:
    top = [r for r in ranks if r <= k]
    if metric in ("hit", "doc_hit"):
        return float(bool(top))
    if metric == "mrr":
        return 1 / min(top) if top else 0.0
    if metric == "recall":
        return len(top) / n_relevant
    return _dcg(ranks, k) / _dcg(list(range(1, n_relevant + 1)), k)  # ndcg, binary gains


@dataclass(frozen=True)
class RankScorer:
    metric: str
    k: int
    params: dict[str, Any] = field(default_factory=dict)
    version: str = "1"

    @property
    def spec(self) -> Spec:
        name = f"{self.metric}@{self.k}"
        return name if not self.params else {name: self.params}

    @cached_property
    def name(self) -> str:
        return spec_name(self.spec)

    @property
    def verdict(self) -> Verdict:
        return "value" if self.metric in _BINARY else "none"  # mrr, ndcg, recall: no verdict

    def _value(self, hits: list[dict[str, Any]], expected: Any) -> tuple[float, dict[str, Any]]:
        if isinstance(expected, dict) and "evidence" in expected:
            answer, doc = evidence_ranks(hits, str(expected["doc"]), str(expected["evidence"]))
            evidence: dict[str, Any] = {"answer_rank": answer, "doc_rank": doc}
        elif isinstance(expected, dict) and "relevant" in expected:
            relevant = {str(r) for r in expected["relevant"]}
            if not relevant:
                raise ValueError("expected.relevant is empty")
            evidence = {
                "relevant_ranks": _relevance_ranks(hits, relevant),
                "relevant": len(relevant),
            }
        else:
            raise ValueError("retrieval expected must be {doc, evidence} or {relevant: [ids]}")
        ranks, n_relevant = ranks_of(evidence, self.metric)
        return metric_at_k(self.metric, self.k, ranks, n_relevant), evidence

    def score(self, case: Case, output: Output) -> Score:
        hits = get_path(output.value, self.params.get("field"))
        if not isinstance(hits, list) or not all(isinstance(h, dict) for h in hits):
            raise ValueError("retrieval output must be a list of hits ({doc, text, id?})")
        value, evidence = self._value(hits, get_path(case.expected, self.params.get("expected")))
        return Score(
            case_id=output.case_id,
            repeat=output.repeat,
            scorer=self.name,
            scorer_version=self.version,
            value=value,
            passed=(value == 1.0) if self.verdict == "value" else None,
            reason=f"{self.metric}@{self.k} = {value:g}",
            evidence=evidence,
        )


def rank_scorer(name: str, params: dict[str, Any]) -> RankScorer | None:
    """A RankScorer when `name` is `<metric>@<k>`, else None."""
    m = NAME.match(name)
    if m is None:
        return None
    unknown = set(params) - {"field", "expected"}
    if unknown:
        raise ConfigError(f"scorer {name!r}: unknown params {sorted(unknown)}")
    return RankScorer(metric=m.group(1), k=int(m.group(2)), params=dict(params))


_RANX = {"hit": "hit_rate", "doc_hit": "hit_rate", "mrr": "mrr", "ndcg": "ndcg", "recall": "recall"}


def ranx_value(scorer: RankScorer, scores: list[Score], case_ids: list[str], repeats: int) -> float:
    """The run-level metric recomputed by ranx from the ranks in each score's evidence (design
    §18.17): per repeat, then averaged, as the summary averages. A case without ranks (a target
    or scoring error, or no output) retrieved nothing."""
    from ranx import Qrels, Run, evaluate  # the [ranking] extra

    by_key = {(s.case_id, s.repeat): s for s in scores if s.scorer == scorer.name}
    k = scorer.k
    per_repeat = []
    for repeat in range(repeats):
        qrels: dict[str, dict[str, int]] = {}
        run: dict[str, dict[str, float]] = {}
        for cid in case_ids:
            score = by_key.get((cid, repeat))
            ev = score.evidence if score and isinstance(score.evidence, dict) else {}
            ranks, n_rel = ranks_of(ev, scorer.metric)
            qrels[cid] = {f"rel{i}": 1 for i in range(n_rel)}
            placed = dict(zip(ranks, (f"rel{i}" for i in range(n_rel)), strict=False))
            depth = max([k, *ranks])
            run[cid] = {placed.get(r, f"other{r}"): 1.0 / r for r in range(1, depth + 1)}
        metric = f"{_RANX[scorer.metric]}@{k}"
        per_repeat.append(float(evaluate(Qrels.from_dict(qrels), Run.from_dict(run), metric)))
    return sum(per_repeat) / len(per_repeat)
