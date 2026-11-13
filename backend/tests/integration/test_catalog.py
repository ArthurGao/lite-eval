"""The Viewer's run catalog (design §18.15): the files and the SQLite index answer the same
queries the same way -- one set of read tests, run against both."""

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lite_eval.errors import ConfigError
from lite_eval.index import FileCatalog, IndexCatalog, RunCatalog, RunFilter
from lite_eval.runner import RunResult
from lite_eval.store import load_run, set_baseline
from lite_eval.targets.fake import FakeTarget
from tests.helpers import execute_one, wrong

CASES = [{"id": f"q{i}", "input": i, "expected": f"a{i}"} for i in range(4)]


def run(root: Path, config: str, target: FakeTarget, **spec: Any) -> RunResult:
    return execute_one(root, config, target, cases=CASES, **spec)


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """demo-suite: a (4/4), b (2/4), then a again on dataset version 2 (3/4); `b` is the
    baseline after two switches. other-suite: one retrieval-pack-free run. Plus a directory
    still running (no run.json)."""
    root = tmp_path_factory.mktemp("evals")
    first = run(root, "a", wrong())
    second = run(root, "b", wrong("q1", "q2"))
    third = run(root, "a", wrong("q3"), dataset_meta={"version": "2"})
    run(root, "x", wrong("q0"), name="other-suite")
    set_baseline(load_run(first.directory))
    set_baseline(load_run(second.directory))
    (root / "runs" / "demo-suite" / "20991231T000000000000Z-running-000000").mkdir()
    assert third.record.dataset.version == "2"
    return root


@pytest.fixture(params=["files", "index"])
def catalog(request: pytest.FixtureRequest, root: Path) -> Iterator[RunCatalog]:
    if request.param == "files":
        yield FileCatalog(root)
        return
    index = IndexCatalog.open(root)
    try:
        yield index
    finally:
        index.close()


def test_runs_are_listed_newest_first_with_their_numbers(catalog: RunCatalog) -> None:
    page = catalog.list_runs(RunFilter())

    assert page.total == 4
    assert [(r.suite, r.config) for r in page.items] == [
        ("other-suite", "x"),
        ("demo-suite", "a"),
        ("demo-suite", "b"),
        ("demo-suite", "a"),
    ]
    other, latest, baseline, oldest = page.items
    assert latest.metrics == {"exact_match": 0.75}
    assert baseline.metrics == {"exact_match": 0.5} and oldest.metrics == {"exact_match": 1.0}
    assert [r.baseline for r in page.items] == [False, False, True, False]
    assert latest.dataset_version == "2" and oldest.dataset_version == "1"
    assert latest.n_cases == 4 and latest.case_weight == 0.25
    assert latest.status == "complete" and latest.warnings == 0
    assert latest.calls == 5  # 4 cases + the untimed warm-up, which is charged (§18.18)
    assert other.target == "fake"


def test_filters_and_pages(catalog: RunCatalog) -> None:
    demo = catalog.list_runs(RunFilter(suite="demo-suite"))
    assert demo.total == 3 and {r.suite for r in demo.items} == {"demo-suite"}

    page = catalog.list_runs(RunFilter(suite="demo-suite"), offset=1, limit=1)
    assert page.total == 3 and [r.config for r in page.items] == ["b"]

    assert catalog.list_runs(RunFilter(config="a")).total == 2
    assert catalog.list_runs(RunFilter(status="partial")).total == 0
    assert catalog.list_runs(RunFilter(dataset="demo", dataset_version="2")).total == 1
    assert catalog.list_runs(RunFilter(baseline=True)).total == 1


def test_a_trend_marks_baseline_switches_and_dataset_versions(catalog: RunCatalog) -> None:
    trend = catalog.trend("demo-suite", "exact_match")

    assert [(p.config, p.value) for p in trend.points] == [("a", 1.0), ("b", 0.5), ("a", 0.75)]
    assert [p.baseline for p in trend.points] == [False, True, False]
    first, second, third = (p.run_id for p in trend.points)
    assert [(c.run_id, c.previous) for c in trend.baseline_changes] == [
        (first, None),
        (second, first),
    ]
    assert [(c.run_id, c.version) for c in trend.dataset_changes] == [(third, "2")]
    assert catalog.trend("demo-suite", "exact_match", config="a").points[1].run_id == third


def test_a_trend_of_an_unknown_metric_has_no_points(catalog: RunCatalog) -> None:
    assert catalog.trend("demo-suite", "no_such_metric").points == []


# ---------------------------------------------------------------- the index only


def test_the_index_adds_changes_and_drops_runs_incrementally(tmp_path: Path) -> None:
    first = run(tmp_path, "a", wrong())
    index = IndexCatalog.open(tmp_path)
    try:
        assert index.list_runs(RunFilter()).total == 1

        second = run(tmp_path, "b", wrong("q0"))
        assert index.list_runs(RunFilter()).total == 1  # within the refresh interval
        index.refresh(force=True)
        assert index.list_runs(RunFilter()).total == 2

        run_json = second.directory / "run.json"
        data = json.loads(run_json.read_text())
        data["status"] = "partial"
        run_json.write_text(json.dumps(data))
        os.utime(run_json, ns=(1, 1))  # a changed mtime and size
        (first.directory / "run.json").unlink()
        index.refresh(force=True)

        rows = index.list_runs(RunFilter()).items
        assert [(r.config, r.status) for r in rows] == [("b", "partial")]
    finally:
        index.close()


def test_the_index_refreshes_on_a_query_after_five_seconds(tmp_path: Path) -> None:
    now = [100.0]
    run(tmp_path, "a", wrong())
    index = IndexCatalog.open(tmp_path, clock=lambda: now[0])
    try:
        run(tmp_path, "b", wrong("q0"))
        now[0] += 4.9
        assert index.list_runs(RunFilter()).total == 1
        now[0] += 0.2
        assert index.list_runs(RunFilter()).total == 2
    finally:
        index.close()


def test_a_second_viewer_on_the_same_root_is_refused(tmp_path: Path) -> None:
    index = IndexCatalog.open(tmp_path)
    try:
        with pytest.raises(ConfigError, match="another Viewer"):
            IndexCatalog.open(tmp_path)
    finally:
        index.close()
    IndexCatalog.open(tmp_path).close()  # free again once the first one closed


def test_an_index_of_another_schema_version_is_rebuilt(tmp_path: Path) -> None:
    run(tmp_path, "a", wrong())
    IndexCatalog.open(tmp_path).close()
    path = tmp_path / ".lite-eval" / "index.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
        db.execute("DELETE FROM runs")
    db.close()

    index = IndexCatalog.open(tmp_path)
    try:
        assert index.list_runs(RunFilter()).total == 1
    finally:
        index.close()
    with sqlite3.connect(path) as db:
        (version,) = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    db.close()
    assert version == str(IndexCatalog.SCHEMA_VERSION)
