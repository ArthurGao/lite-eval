"""LongMemEval (`longmemeval_{s,m,oracle}_cleaned.json`, MIT) into cases.

One case per question, id = `question_id`. The input is what a memory system is given: the question,
its date and the haystack sessions in file order (`has_answer` is a label and is dropped). Expected
is `{relevant: answer_session_ids, answer}`, so session recall is the retrieval pack's `recall@k`.
Abstention questions (`_abs` id suffix) are skipped unless asked for, as the benchmark's own
retrieval evaluation skips them; kept ones are tagged `abstention: "true"`.
"""

from typing import Any

from lite_eval.contracts import Case
from lite_eval.datasets.loaders._common import Loaded, Source, field_of, text
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


def read(items: list[Any], limit: int | None, include_abstention: bool) -> Loaded:
    cases: list[Case] = []
    skipped = 0
    for n, item in enumerate(items):
        qid = str(field_of(item, "question_id", f"question #{n}"))
        where = f"question {qid!r}"
        abstention = qid.endswith("_abs")
        if abstention and not include_abstention:
            skipped += 1
            continue
        if limit is not None and len(cases) >= limit:
            break
        tags = {"question_type": str(field_of(item, "question_type", where))}
        if abstention:
            tags["abstention"] = "true"
        cases.append(
            Case(
                id=qid,
                input={
                    "question": field_of(item, "question", where),
                    "question_date": field_of(item, "question_date", where),
                    "sessions": _sessions(item, where),
                },
                expected={
                    "relevant": [str(s) for s in field_of(item, "answer_session_ids", where)],
                    "answer": text(field_of(item, "answer", where)),
                },
                tags=tags,
            )
        )
    notes = [f"skipped {skipped} abstention question(s)"] if skipped else []
    return Loaded(cases=cases, notes=notes)
