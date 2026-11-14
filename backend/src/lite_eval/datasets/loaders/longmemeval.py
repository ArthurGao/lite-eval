"""LongMemEval (`longmemeval_{s,m,oracle}_cleaned.json`, MIT) into cases.

One case per question, id = `question_id`. The input is what a memory system is given: the question,
its date and `haystack: <question_id>`, which names `corpus/<question_id>.json`: the haystack
sessions in file order, `[{id, date, turns: [{role, content}]}]` (`has_answer` is a label and is
dropped). Kept out of the case, so a run holds one haystack at a time (LongMemEval M is 2.7 GB)
and, like every corpus file, the history is not secret-scanned (design §18.12). Expected
is `{relevant: answer_session_ids, answer}`, so session recall is the retrieval pack's `recall@k`.
Abstention questions (`_abs` id suffix) are skipped unless asked for, as the benchmark's own
retrieval evaluation skips them; kept ones are tagged `abstention: "true"`.
"""

from collections.abc import Iterable, Iterator
from typing import Any

from lite_eval.contracts import Case
from lite_eval.datasets.loaders._common import Item, Source, corpus_name, field_of, text
from lite_eval.errors import ConfigError

SOURCE = Source(
    "LongMemEval", "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned", "MIT"
)


def _sessions(item: dict[str, Any], where: str) -> list[dict[str, Any]]:
    ids = field_of(item, "haystack_session_ids", where)
    dates = field_of(item, "haystack_dates", where)
    sessions = field_of(item, "haystack_sessions", where)
    if not len(ids) == len(dates) == len(sessions):
        raise ConfigError(f"{where}: haystack ids, dates and sessions differ in length")
    return [
        {
            "id": str(sid),
            "date": str(date),
            "turns": [
                {"role": field_of(t, "role", where), "content": field_of(t, "content", where)}
                for t in turns
            ],
        }
        for sid, date, turns in zip(ids, dates, sessions, strict=True)
    ]


def read(
    items: Iterable[Any], limit: int | None, include_abstention: bool, notes: list[str]
) -> Iterator[Item]:
    """The cases, one at a time; what was left out is appended to `notes` once they are read."""
    kept = skipped = 0
    for n, item in enumerate(items):
        qid = str(field_of(item, "question_id", f"question #{n}"))
        where = f"question {qid!r}"
        abstention = qid.endswith("_abs")
        if abstention and not include_abstention:
            skipped += 1
            continue
        if limit is not None and kept >= limit:
            break
        tags = {"question_type": str(field_of(item, "question_type", where))}
        if abstention:
            tags["abstention"] = "true"
        kept += 1
        case = Case(
            id=qid,
            input={
                "question": field_of(item, "question", where),
                "question_date": field_of(item, "question_date", where),
                "haystack": corpus_name(qid, "question id"),
            },
            expected={
                "relevant": [str(s) for s in field_of(item, "answer_session_ids", where)],
                "answer": text(field_of(item, "answer", where)),
            },
            tags=tags,
        )
        yield Item(case, {qid: _sessions(item, where)})
    if skipped:  # with a limit, reading stopped there: later ones are not counted
        until = " before the limit" if limit is not None else ""
        notes.append(f"skipped {skipped} abstention question(s){until}")
