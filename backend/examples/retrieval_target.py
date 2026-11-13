"""A tiny retriever over the dataset's corpus, for the retrieval pack example.

`python: examples.retrieval_target:make`. Each paragraph of each corpus file is a chunk; `overlap`
ranks chunks by shared words with the query, `order` returns them in file order (a bad baseline).
"""

import re
from typing import Any

from lite_eval.contracts import Billing, Case, Output, Trace
from lite_eval.datasets.local import Dataset

_WORD = re.compile(r"[a-z0-9]+")


class Retriever:
    name = "toy-retriever"

    def __init__(self, ranking: str = "overlap", top_k: int = 10) -> None:
        self.ranking, self.top_k = ranking, top_k
        self.chunks: list[dict[str, str]] = []

    def config(self) -> dict[str, Any]:
        return {"ranking": self.ranking, "top_k": self.top_k}

    def versions(self) -> dict[str, str]:
        return {"retriever": "1"}

    def info(self) -> dict[str, str]:
        return {}

    def billing(self) -> Billing:
        return "free"

    def setup_identity(self) -> dict[str, Any]:
        return {"chunking": "paragraph"}  # both configs search the same chunks

    async def setup(self, dataset: Dataset) -> None:
        for path in sorted(dataset.corpus_dir.glob("*.md")):
            for para in path.read_text(encoding="utf-8").split("\n\n"):
                if para.strip():
                    self.chunks.append({"doc": path.stem, "text": para.strip()})

    async def share_setup_from(self, leader: "Retriever") -> None:
        self.chunks = leader.chunks

    async def teardown(self) -> None:
        pass

    async def run(self, case: Case) -> Output:
        assert isinstance(case.input, dict)
        words = set(_WORD.findall(str(case.input["query"]).lower()))
        chunks = list(self.chunks)
        if self.ranking == "overlap":
            chunks.sort(key=lambda c: -len(words & set(_WORD.findall(c["text"].lower()))))
        return Output(
            case_id=case.id,
            value=chunks[: self.top_k],
            trace=Trace(latency_scope="in-memory ranking only"),
        )


def make(ranking: str = "overlap", top_k: int = 10) -> Retriever:
    return Retriever(ranking, top_k)
