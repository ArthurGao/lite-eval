"""LoCoMo (`data/locomo10.json`, CC BY-NC 4.0: non-commercial, attribution) into cases.

Each conversation is written once, as `corpus/<sample_id>.json`: speakers and sessions in session
order, each turn as in the file (`dia_id` kept, it is what evidence points at). One case per
question, id = `<sample_id>:q<nnn>` (from 1, in file order), input `{question, conversation:
<sample_id>}`; a target reads the conversation from `dataset.corpus_dir` in `setup`. Copying the
conversation into each of its ~200 questions would make every run hash, scan and send it 200 times.

Expected is `{relevant: evidence dia_ids, answer}`; answers that are numbers become text. Category
5 (adversarial) questions have no answer: `answer` is null and the trap answer is kept apart as
`adversarial_answer`. The category is tagged by its number only: the benchmark does not document
what the numbers mean.
"""

import re
from typing import Any

from lite_eval.contracts import Case
from lite_eval.datasets.loaders._common import Loaded, Source, field_of, text
from lite_eval.errors import ConfigError

SOURCE = Source("LoCoMo", "https://github.com/snap-research/locomo", "CC BY-NC 4.0")
_SESSION = re.compile(r"^session_([0-9]+)$")
_SAMPLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")  # it names a corpus file


def _conversation(conv: dict[str, Any], where: str) -> dict[str, Any]:
    numbers = sorted(int(m.group(1)) for k in conv if (m := _SESSION.match(k)))
    return {
        "speaker_a": field_of(conv, "speaker_a", where),
        "speaker_b": field_of(conv, "speaker_b", where),
        "sessions": [
            {
                "id": f"session_{n}",
                "date": conv.get(f"session_{n}_date_time"),
                "turns": conv[f"session_{n}"],
            }
            for n in numbers
        ],
    }


def read(items: list[Any], limit: int | None) -> Loaded:
    cases: list[Case] = []
    corpus: dict[str, Any] = {}
    for n, sample in enumerate(items):
        sid = str(field_of(sample, "sample_id", f"sample #{n}"))
        if not _SAMPLE_ID.match(sid):
            raise ConfigError(f"sample id {sid!r} cannot name a corpus file")
        for i, qa in enumerate(field_of(sample, "qa", sid), start=1):
            if limit is not None and len(cases) >= limit:
                return Loaded(cases=cases, corpus=corpus)
            where = f"{sid} question {i}"
            if sid not in corpus:  # only conversations some kept question points at
                corpus[sid] = _conversation(field_of(sample, "conversation", sid), sid)
            expected: dict[str, Any] = {
                "relevant": [str(e) for e in field_of(qa, "evidence", where)],
                "answer": text(qa.get("answer")),
            }
            if "adversarial_answer" in qa:
                expected["adversarial_answer"] = text(qa["adversarial_answer"])
            cases.append(
                Case(
                    id=f"{sid}:q{i:03d}",
                    input={"question": field_of(qa, "question", where), "conversation": sid},
                    expected=expected,
                    tags={"category": str(field_of(qa, "category", where))},
                )
            )
    return Loaded(cases=cases, corpus=corpus)
