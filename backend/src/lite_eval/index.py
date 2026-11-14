"""The Viewer's run catalog (design §10.2, §18.15).

The run directories are the truth. `FileCatalog` answers every query by reading them;
`IndexCatalog` answers the same queries from `.lite-eval/index.sqlite`, which it keeps in step
with the directories and which can be deleted at any time. Both build their rows with `run_row`,
and the tests run one set of read tests against both.

Which run is a suite's baseline is read from `baselines.yaml` at query time, never indexed:
switching a baseline does not touch any run.json.
"""

import fcntl
import itertools
import os
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import IO, Protocol

from pydantic import BaseModel, ConfigDict

from lite_eval.contracts import BaselineChange, RunRecord, RunStatus
from lite_eval.errors import ConfigError
from lite_eval.store import read_baseline_changes, read_baselines, read_record, runs_dir

INDEX_DIR = ".lite-eval"
REFRESH_S = 5.0  # §18.15: rescan at most this often, when a query comes in


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RunRow(_Model):
    """One run as the Runs page lists it."""

    suite: str
    run_id: str
    config: str
    target: str
    dataset: str
    dataset_version: str
    dataset_hash: str
    pack: str | None
    synthetic: bool
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    git_sha: str | None
    metrics: dict[str, float]
    n_cases: int
    case_weight: float
    calls: int
    cost_usd: float
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    warnings: int
    target_errors: int
    scoring_errors: int
    baseline: bool = False


class RunFilter(_Model):
    suite: str | None = None
    config: str | None = None
    status: RunStatus | None = None
    pack: str | None = None
    dataset: str | None = None
    dataset_version: str | None = None
    baseline: bool | None = None


class RunPage(_Model):
    items: list[RunRow]
    total: int
    offset: int
    limit: int


class TrendPoint(_Model):
    run_id: str
    config: str
    started_at: datetime
    git_sha: str | None
    value: float
    dataset_version: str
    dataset_hash: str
    baseline: bool
    # every doubt the run carries, so a trend's table marks it like the Runs list (rule 3)
    status: RunStatus
    warnings: int
    synthetic: bool


class DatasetChange(_Model):
    """The first point of a trend measured on another dataset content than the point before."""

    run_id: str
    version: str
    hash: str
    previous_version: str


class Trend(_Model):
    suite: str
    metric: str
    points: list[TrendPoint]  # oldest first
    baseline_changes: list[BaselineChange]
    dataset_changes: list[DatasetChange]


def run_row(record: RunRecord) -> RunRow:
    s, c = record.summary, record.cost
    return RunRow(
        suite=record.suite.name,
        run_id=record.run_id,
        config=str(record.target.config.get("name", "")),
        target=record.target.name,
        dataset=record.dataset.name,
        dataset_version=record.dataset.version,
        dataset_hash=record.dataset.hash,
        pack=record.dataset.pack,
        synthetic=record.dataset.synthetic,
        status=record.status,
        started_at=record.started_at,
        finished_at=record.finished_at,
        git_sha=record.git_sha,
        metrics={name: m.value for name, m in s.metrics.items()},
        n_cases=s.n_cases,
        case_weight=s.case_weight,
        calls=c.calls,
        cost_usd=c.cost_usd,
        latency_p50_ms=c.latency_p50_ms,
        latency_p95_ms=c.latency_p95_ms,
        warnings=len(record.warnings),
        target_errors=s.errored_outputs,
        scoring_errors=s.scoring_error_count,
    )


class RunCatalog(Protocol):
    def list_runs(self, filters: RunFilter, offset: int = 0, limit: int | None = 50) -> RunPage: ...

    def trend(self, suite: str, metric: str, config: str | None = None) -> Trend: ...


def _matches(row: RunRow, f: RunFilter) -> bool:
    return (
        (f.suite is None or row.suite == f.suite)
        and (f.config is None or row.config == f.config)
        and (f.status is None or row.status == f.status)
        and (f.pack is None or row.pack == f.pack)
        and (f.dataset is None or row.dataset == f.dataset)
        and (f.dataset_version is None or row.dataset_version == f.dataset_version)
        and (f.baseline is None or row.baseline == f.baseline)
    )


def _marked(root: Path, rows: Iterable[RunRow]) -> list[RunRow]:
    """Rows with today's baseline flag, oldest first."""
    baselines = read_baselines(root)
    marked = [r.model_copy(update={"baseline": baselines.get(r.suite) == r.run_id}) for r in rows]
    return sorted(marked, key=lambda r: (r.started_at, r.run_id))


class _Catalog:
    """Filtering, order, pages and trends, written once: a catalog only says where its rows
    come from (`_rows`), so the files and the index cannot answer differently."""

    root: Path

    def _rows(self) -> Iterable[RunRow]:
        raise NotImplementedError

    def list_runs(self, filters: RunFilter, offset: int = 0, limit: int | None = 50) -> RunPage:
        rows = [r for r in reversed(_marked(self.root, self._rows())) if _matches(r, filters)]
        size = len(rows) if limit is None else limit
        return RunPage(
            items=rows[offset : offset + size], total=len(rows), offset=offset, limit=size
        )

    def trend(self, suite: str, metric: str, config: str | None = None) -> Trend:
        rows = [
            r
            for r in _marked(self.root, self._rows())
            if r.suite == suite and config in (None, r.config)
        ]
        points = [
            TrendPoint(
                run_id=r.run_id,
                config=r.config,
                started_at=r.started_at,
                git_sha=r.git_sha,
                value=r.metrics[metric],
                dataset_version=r.dataset_version,
                dataset_hash=r.dataset_hash,
                baseline=r.baseline,
                status=r.status,
                warnings=r.warnings,
                synthetic=r.synthetic,
            )
            for r in rows
            if metric in r.metrics
        ]
        return Trend(
            suite=suite,
            metric=metric,
            points=points,
            baseline_changes=[c for c in read_baseline_changes(self.root) if c.suite == suite],
            dataset_changes=[
                DatasetChange(
                    run_id=p.run_id,
                    version=p.dataset_version,
                    hash=p.dataset_hash,
                    previous_version=q.dataset_version,
                )
                for q, p in itertools.pairwise(points)
                if p.dataset_hash != q.dataset_hash
            ],
        )


def _run_files(root: Path) -> list[Path]:
    """Every finished run's run.json; a directory without one is still running (§18.15)."""
    return sorted(runs_dir(root).glob("*/*/run.json"))


def _row(path: Path) -> RunRow | None:
    try:
        return run_row(read_record(path.parent))
    except (OSError, ConfigError):
        return None  # vanished or half-written by hand: not a run the Viewer can show


class FileCatalog(_Catalog):
    """Answers from the run directories alone; the reference the index is tested against."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _rows(self) -> Iterable[RunRow]:
        return [row for path in _run_files(self.root) if (row := _row(path)) is not None]


_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE runs (
    suite TEXT NOT NULL,
    run_id TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    row TEXT NOT NULL,
    PRIMARY KEY (suite, run_id)
);
"""


class IndexCatalog(_Catalog):
    """The SQLite index: one stored row per run, kept in step by stat (§18.15). One Viewer per
    evaluation root: `open` takes `.lite-eval/index.lock` and refuses when another process
    holds it."""

    SCHEMA_VERSION = 1

    def __init__(
        self, root: Path, db: sqlite3.Connection, lock: IO[str], clock: Callable[[], float]
    ) -> None:
        self.root = root
        self._db = db
        self._lock_file = lock
        self._clock = clock
        self._mutex = threading.Lock()  # FastAPI runs sync handlers on a thread pool
        self._scanned_at = clock()

    @classmethod
    def open(cls, root: Path, clock: Callable[[], float] = time.monotonic) -> "IndexCatalog":
        directory = root / INDEX_DIR
        directory.mkdir(parents=True, exist_ok=True)
        lock = (directory / "index.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise ConfigError(
                f"another Viewer is using {root} ({INDEX_DIR}/index.lock); stop it first"
            ) from None
        try:
            path = directory / "index.sqlite"
            if _version(path) != str(cls.SCHEMA_VERSION):
                _rebuild(path, cls.SCHEMA_VERSION)
            db = sqlite3.connect(path, check_same_thread=False)
        except BaseException:
            lock.close()
            raise
        catalog = cls(root, db, lock, clock)
        catalog._scan()
        return catalog

    def close(self) -> None:
        self._db.close()
        fcntl.flock(self._lock_file, fcntl.LOCK_UN)
        self._lock_file.close()

    def refresh(self, *, force: bool = False) -> None:
        """Bring the index in step with the run directories: new, changed, gone (§18.15)."""
        with self._mutex:
            if force or self._clock() - self._scanned_at >= REFRESH_S:
                self._scan()

    def _scan(self) -> None:
        known = {
            (suite, run_id): (mtime, size)
            for suite, run_id, mtime, size in self._db.execute(
                "SELECT suite, run_id, mtime_ns, size FROM runs"
            )
        }
        seen: set[tuple[str, str]] = set()
        with self._db:
            for path in _run_files(self.root):
                key = (path.parent.parent.name, path.parent.name)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if known.get(key) == (stat.st_mtime_ns, stat.st_size):
                    seen.add(key)
                    continue
                row = _row(path)
                if row is None:
                    continue
                seen.add((row.suite, row.run_id))
                self._db.execute(
                    "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?)",
                    (row.suite, row.run_id, stat.st_mtime_ns, stat.st_size, row.model_dump_json()),
                )
            for key in known.keys() - seen:
                self._db.execute("DELETE FROM runs WHERE suite = ? AND run_id = ?", key)
        self._scanned_at = self._clock()

    def _rows(self) -> Iterable[RunRow]:
        self.refresh()
        with self._mutex:
            fetched = self._db.execute("SELECT row FROM runs").fetchall()
        return [RunRow.model_validate_json(r) for (r,) in fetched]


def _version(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        db = sqlite3.connect(path)
        try:
            found = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        finally:
            db.close()
    except sqlite3.DatabaseError:
        return None  # not an index this version can read: rebuild it
    return None if found is None else str(found[0])


def _rebuild(path: Path, version: int) -> None:
    """A fresh, empty index of `version`, built aside and renamed into place (§18.15); the
    first scan fills it."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".index-", suffix=".sqlite")
    os.close(fd)
    db = sqlite3.connect(tmp)
    try:
        db.executescript(_SCHEMA)
        db.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(version),))
        db.commit()
    finally:
        db.close()
    os.replace(tmp, path)
