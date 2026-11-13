"""What the Viewer and the HTML export show (design §10.1): run detail, per-case rows, one case
with its content from the dataset (by hash, §18.9), datasets, comparisons; redacted again on the
way out (§18.12)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lite_eval.annotations import append_annotation, new_annotation_id
from lite_eval.contracts import Annotation
from lite_eval.errors import NotFound
from lite_eval.index import FileCatalog
from lite_eval.runner import RunResult
from lite_eval.targets.fake import FakeTarget, Step
from lite_eval.views import (
    case_detail,
    case_page,
    comparison,
    dataset_cases,
    datasets,
    run_detail,
)
from tests.helpers import execute_one, execute_suite, write_dataset, wrong

CASES = [
    {"id": "q0", "input": {"query": "zero"}, "expected": "a0", "tags": {"kind": "easy"}},
    {"id": "q1", "input": {"query": "one"}, "expected": "a1", "tags": {"kind": "hard"}},
    {"id": "q2", "input": {"query": "two"}, "expected": "a2", "tags": {"kind": "hard"}},
    {"id": "q3", "input": {"query": "three"}, "expected": "a3", "tags": {"kind": "easy"}},
]
SECRET = "sk-" + "A" * 24


def run(root: Path, config: str, target: FakeTarget) -> RunResult:
    return execute_one(root, config, target, cases=CASES, redact=["api_keys"])


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def runs(root: Path) -> tuple[RunResult, RunResult]:
    a = run(root, "a", wrong("q1"))
    b = run(root, "b", wrong("q2", "q3"))
    return a, b


def triage(root: Path, result: RunResult, case_id: str, **fields: Any) -> Annotation:
    now = datetime(2026, 11, 14, tzinfo=UTC)
    return append_annotation(
        root,
        Annotation(
            annotation_id=new_annotation_id(now),
            dataset="demo",
            case_id=case_id,
            case_hash=result.record.case_hashes[case_id],
            run_id=result.record.run_id,
            author="tester",
            created_at=now,
            **{"category": "real_miss", **fields},
        ),
    )


def ids(result: RunResult) -> tuple[str, str]:
    return result.record.suite.name, result.record.run_id


def test_run_detail_returns_the_record_and_its_baseline_flag(
    root: Path, runs: tuple[RunResult, RunResult]
) -> None:
    a, _ = runs
    detail = run_detail(root, *ids(a))

    assert detail.record.run_id == a.record.run_id
    assert detail.record.summary.metrics["exact_match"].value == 0.75
    assert detail.baseline is False
    with pytest.raises(NotFound):
        run_detail(root, "demo-suite", "no-such-run")
    with pytest.raises(NotFound):  # would resolve to the real run without the name check
        run_detail(root, "..", f"runs/demo-suite/{a.record.run_id}")


def test_case_rows_mark_misses_and_carry_annotations(
    root: Path, runs: tuple[RunResult, RunResult]
) -> None:
    a, _ = runs
    triage(root, a, "q1", note="retriever missed it")

    page = case_page(root, *ids(a))
    assert page.total == 4
    assert [(r.case_id, r.miss, r.scores) for r in page.items] == [
        ("q0", False, {"exact_match": 1.0}),
        ("q1", True, {"exact_match": 0.0}),
        ("q2", False, {"exact_match": 1.0}),
        ("q3", False, {"exact_match": 1.0}),
    ]
    assert page.items[1].tags == {"kind": "hard"}
    assert page.items[1].annotation is not None
    assert page.items[1].annotation.note == "retriever missed it"
    assert [r.annotation for r in page.items if r.case_id != "q1"] == [None] * 3

    misses = case_page(root, *ids(a), misses_only=True)
    assert misses.total == 1 and [r.case_id for r in misses.items] == ["q1"]
    assert [r.case_id for r in case_page(root, *ids(a), offset=2, limit=1).items] == ["q2"]


def test_an_errored_output_is_a_miss(root: Path) -> None:
    (result,) = execute_suite(
        root, [("e", FakeTarget(steps={"q3": Step(error="timeout")}))], cases=CASES
    )
    rows = case_page(root, *ids(result)).items
    assert [(r.case_id, r.miss, r.error) for r in rows][2:] == [
        ("q2", False, None),
        ("q3", True, "timeout"),
    ]


def test_case_detail_shows_the_case_its_outputs_and_other_runs(
    root: Path, runs: tuple[RunResult, RunResult]
) -> None:
    a, b = runs
    detail = case_detail(root, *ids(a), "q1", FileCatalog(root))

    assert detail.content.status == "current"
    assert detail.content.case is not None
    assert detail.content.case.input == {"query": "one"} and detail.content.case.expected == "a1"
    assert [(o.repeat, o.value) for o in detail.outputs] == [(0, "no")]
    assert [(s.scorer, s.value, s.passed) for s in detail.scores] == [("exact_match", 0.0, False)]
    assert [(o.run_id, o.config, o.miss) for o in detail.other_runs] == [
        (b.record.run_id, "b", False)
    ]
    with pytest.raises(NotFound):
        case_detail(root, *ids(a), "q9", FileCatalog(root))


def test_content_that_changed_since_the_run_is_marked(
    root: Path, runs: tuple[RunResult, RunResult]
) -> None:
    a, _ = runs
    edited = [dict(c, expected="A1") if c["id"] == "q1" else c for c in CASES if c["id"] != "q3"]
    write_dataset(root / "datasets" / "demo", edited)

    changed = case_detail(root, *ids(a), "q1", FileCatalog(root)).content
    assert changed.status == "changed"
    assert changed.case is not None and changed.case.expected == "A1"  # today's content
    gone = case_detail(root, *ids(a), "q3", FileCatalog(root)).content
    assert gone.status == "missing" and gone.case is None
    assert case_detail(root, *ids(a), "q0", FileCatalog(root)).content.status == "current"


def test_an_old_annotation_is_shown_as_one_for_an_older_version(
    root: Path, runs: tuple[RunResult, RunResult]
) -> None:
    a, _ = runs
    old = triage(root, a, "q1")
    edited = [dict(c, expected="A1") if c["id"] == "q1" else c for c in CASES]
    write_dataset(root / "datasets" / "demo", edited)
    (rerun,) = execute_suite(root, [("c", wrong("q1"))], cases=edited)

    detail = case_detail(root, *ids(rerun), "q1", FileCatalog(root))
    assert detail.annotation is None  # not carried over to new content (§18.9)
    assert detail.other_runs == []  # run a saw other content: not the same question
    assert [x.annotation_id for x in detail.older_annotations] == [old.annotation_id]
    row = case_page(root, *ids(rerun), misses_only=True).items[0]
    assert row.annotation is None and row.older_annotation is True

    current = triage(root, rerun, "q1", category="label_wrong")
    detail = case_detail(root, *ids(rerun), "q1", FileCatalog(root))
    assert detail.annotation == current
    assert [x.annotation_id for x in detail.older_annotations] == [old.annotation_id]


def test_outputs_are_redacted_again_on_the_way_out(
    root: Path, runs: tuple[RunResult, RunResult]
) -> None:
    a, _ = runs
    outputs = a.directory / "outputs.jsonl"
    lines = [json.loads(line) for line in outputs.read_text().splitlines()]
    lines[0]["value"] = f"key {SECRET}"  # as if written before the rule existed
    outputs.write_text("".join(json.dumps(x) + "\n" for x in lines))

    detail = case_detail(root, *ids(a), lines[0]["case_id"], FileCatalog(root))
    assert detail.outputs[0].value == "key «redacted:api_keys»"


def test_datasets_list_their_cases_and_annotations(
    root: Path, runs: tuple[RunResult, RunResult]
) -> None:
    a, _ = runs
    triage(root, a, "q2", category="label_wrong")

    (summary,) = datasets(root)
    assert (summary.name, summary.version, summary.n_cases) == ("demo", "1", 4)
    assert summary.hash == a.record.dataset.hash
    page = dataset_cases(root, "demo", offset=1, limit=2)
    assert page.total == 4 and [c.case.id for c in page.items] == ["q1", "q2"]
    assert page.items[1].annotation is not None
    assert page.items[1].annotation.category == "label_wrong"
    with pytest.raises(NotFound):
        dataset_cases(root, "nope")


def test_a_comparison_by_run_ids(root: Path, runs: tuple[RunResult, RunResult]) -> None:
    a, b = runs
    c = comparison(root, ids(a), ids(b))

    assert c.baseline_run == a.record.run_id and c.candidate_run == b.record.run_id
    assert [(x.case_id, x.kind) for x in c.changes] == [
        ("q2", "regressed"),
        ("q3", "regressed"),
        ("q1", "fixed"),
    ]


def test_cases_a_partial_run_never_generated_are_misses_scoring_zero(root: Path) -> None:
    """A budget stop leaves cases without outputs; they stay in the denominator (rule 4), as
    in the run's own summary."""
    (result,) = execute_suite(
        root, [("p", wrong())], cases=CASES, budget={"max_calls": 3}, concurrency=1
    )
    assert result.record.status == "partial"

    rows = case_page(root, *ids(result)).items
    assert [(r.case_id, r.miss, r.error, r.scores) for r in rows] == [
        ("q0", False, None, {"exact_match": 1.0}),
        ("q1", False, None, {"exact_match": 1.0}),
        ("q2", True, "not_generated", {"exact_match": 0.0}),
        ("q3", True, "not_generated", {"exact_match": 0.0}),
    ]
    mean = sum(r.scores["exact_match"] for r in rows) / len(rows)
    assert mean == result.record.summary.metrics["exact_match"].value == 0.5


def test_a_case_knows_its_neighbours_dataset_and_outcome(
    root: Path, runs: tuple[RunResult, RunResult]
) -> None:
    """The case page needs no case list: prev / next come with the case (no 1000-row fetch)."""
    a, _ = runs
    first = case_detail(root, *ids(a), "q0", FileCatalog(root))
    middle = case_detail(root, *ids(a), "q1", FileCatalog(root))
    last = case_detail(root, *ids(a), "q3", FileCatalog(root))
    assert (first.prev_case_id, first.next_case_id) == (None, "q1")
    assert (middle.prev_case_id, middle.next_case_id) == ("q0", "q2")
    assert (last.prev_case_id, last.next_case_id) == ("q2", None)
    assert (middle.dataset, middle.miss, middle.error) == ("demo", True, None)
    assert first.miss is False
