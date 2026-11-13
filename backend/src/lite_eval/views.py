"""What the Viewer and the HTML export show (design §10.1, §18.22), read from the run directories.

Loading and shaping are separate: `open_run` loads a run, its dataset's cases and nothing else
once; the builders (`rows`, `detail`) shape pages from what was loaded. The HTTP routes wrap
one load per request; the export and `lite-eval triage` load once and build every case from it.

Redaction (§18.12) is typed: outputs, scores, warnings and the target's config and info go
through a `Redactor` -- the run's own rules unless the caller passes another (the export passes
the union of its runs' rules). Ids, hashes and dataset content are never rewritten (datasets
are scanned when loaded instead).

Case content is looked up in the dataset by the hash the run recorded (§18.9): a case whose
content changed since the run is marked, not silently shown as what the run saw.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lite_eval.analysis.compare import compare
from lite_eval.analysis.summary import CaseResult, case_results
from lite_eval.annotations import Key, current_annotations, read_annotations
from lite_eval.contracts import Annotation, Case, Comparison, Output, RunRecord, Score
from lite_eval.datasets.local import load_dataset, read_cases, read_meta
from lite_eval.errors import ConfigError, NotFound
from lite_eval.index import RunCatalog, RunFilter, RunRow
from lite_eval.redact import Redactor
from lite_eval.store import StoredRun, load_run, read_baselines, read_record, run_path

OTHER_RUNS = 20  # newest runs of the same case shown on a case page


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RunDetail(_Model):
    record: RunRecord
    baseline: bool


class CaseRow(_Model):
    case_id: str
    tags: dict[str, str]
    miss: bool  # §18.9; as the run's summary counts it (analysis.summary.case_results)
    error: str | None  # the first error kind; "not_generated" when a repeat has no output
    scores: dict[str, float]  # scorer -> mean over repeats, a missing repeat counting 0
    annotation: Annotation | None  # current, for the content this run saw
    older_annotation: bool  # one exists for an older content of this case


class CasePage(_Model):
    items: list[CaseRow]
    total: int
    offset: int
    limit: int


class CaseContent(_Model):
    """`current`: the dataset holds the content the run saw; `changed`: it holds other content
    now (shown); `missing`: the dataset or the case is gone."""

    status: Literal["current", "changed", "missing"]
    case: Case | None


class OtherRun(_Model):
    suite: str
    run_id: str
    config: str
    started_at: datetime
    miss: bool
    scores: dict[str, float]


class CaseDetail(_Model):
    suite: str
    run_id: str
    case_id: str
    case_hash: str
    content: CaseContent
    outputs: list[Output]
    scores: list[Score]
    annotation: Annotation | None
    older_annotations: list[Annotation]
    other_runs: list[OtherRun]  # same dataset, same content, newest first


class DatasetSummary(_Model):
    name: str
    version: str
    hash: str
    pack: str | None
    description: str
    license: str | None
    synthetic: bool
    n_cases: int
    path: str  # relative to the evaluation root


class DatasetCase(_Model):
    case: Case
    case_hash: str
    annotation: Annotation | None


class DatasetCasePage(_Model):
    dataset: DatasetSummary
    items: list[DatasetCase]
    total: int
    offset: int
    limit: int


def window[T](items: Sequence[T], offset: int, limit: int | None) -> tuple[list[T], int, int]:
    """(page, offset, limit) of `items`; `limit=None` is every item."""
    size = len(items) if limit is None else limit
    return list(items[offset : offset + size]), offset, size


# ---------------------------------------------------------------- loading


@dataclass(frozen=True)
class Notes:
    """The annotations file, read once: current records by key, and by case for older content."""

    current: dict[Key, Annotation]
    by_case: dict[tuple[str, str], list[Annotation]]

    @classmethod
    def load(cls, root: Path) -> "Notes":
        current = current_annotations(read_annotations(root))
        by_case: dict[tuple[str, str], list[Annotation]] = defaultdict(list)
        for a in current.values():
            by_case[a.dataset, a.case_id].append(a)
        return cls(current, dict(by_case))

    def get(self, dataset: str, case_id: str, case_hash: str) -> Annotation | None:
        return self.current.get((dataset, case_id, case_hash))

    def older(self, dataset: str, case_id: str, case_hash: str) -> list[Annotation]:
        return [a for a in self.by_case.get((dataset, case_id), []) if a.case_hash != case_hash]


@dataclass(frozen=True)
class RunView:
    """One run, loaded once: outputs, scores, per-case results and its dataset's cases."""

    root: Path
    stored: StoredRun
    cases: dict[str, Case]  # the dataset's cases today; empty when the dataset is gone
    results: dict[str, CaseResult]
    redactor: Redactor

    @property
    def record(self) -> RunRecord:
        return self.stored.record


def _dataset_dir(root: Path, name: str) -> Path | None:
    """The directory under `<root>/datasets/` whose dataset.yaml names `name` (reads only the
    yaml: no corpus hash, no secret scan)."""
    for meta_path in sorted((root / "datasets").glob("*/dataset.yaml")):
        try:
            if read_meta(meta_path.parent).name == name:
                return meta_path.parent
        except ConfigError:
            continue
    return None


def _cases(root: Path, name: str) -> dict[str, Case]:
    directory = _dataset_dir(root, name)
    if directory is None:
        return {}
    try:
        return read_cases(directory)
    except ConfigError:
        return {}


def open_run(root: Path, suite: str, run_id: str, redactor: Redactor | None = None) -> RunView:
    stored = load_run(run_path(root, suite, run_id), outputs=True)
    record = stored.record
    return RunView(
        root=root,
        stored=stored,
        cases=_cases(root, record.dataset.name),
        results=case_results(record, stored.outputs, stored.scores),
        redactor=redactor or Redactor.from_config(record.redact),
    )


def redacted_record(record: RunRecord, redactor: Redactor) -> RunRecord:
    """Warnings, the target's config and info: the record's free text. Ids and hashes stay."""
    if not redactor.active:
        return record
    target = record.target.model_copy(
        update={
            "config": redactor.json(record.target.config),
            "info": {k: redactor.text(v) for k, v in record.target.info.items()},
        }
    )
    warnings = [w.model_copy(update={"message": redactor.text(w.message)}) for w in record.warnings]
    return record.model_copy(update={"target": target, "warnings": warnings})


# ---------------------------------------------------------------- builders


def rows(view: RunView, notes: Notes, *, misses_only: bool = False) -> list[CaseRow]:
    record = view.record
    out = []
    for case_id, result in view.results.items():
        if misses_only and not result.miss:
            continue
        case_hash = record.case_hashes.get(case_id, "")
        case = view.cases.get(case_id)
        out.append(
            CaseRow(
                case_id=case_id,
                tags=case.tags if case else {},
                miss=result.miss,
                error=result.error,
                scores=result.scores,
                annotation=notes.get(record.dataset.name, case_id, case_hash),
                older_annotation=bool(notes.older(record.dataset.name, case_id, case_hash)),
            )
        )
    return out


def _content(case: Case | None, case_hash: str) -> CaseContent:
    if case is None:
        return CaseContent(status="missing", case=None)
    return CaseContent(
        status="current" if case.content_hash() == case_hash else "changed", case=case
    )


def detail(
    view: RunView, case_id: str, notes: Notes, candidates: Sequence[RunRow] = ()
) -> CaseDetail:
    """One case; `candidates` are the runs to look in for its other results (newest first)."""
    record = view.record
    if case_id not in view.results:
        raise NotFound(f"run {record.suite.name}/{record.run_id} has no case {case_id!r}")
    case_hash = record.case_hashes.get(case_id, "")
    dataset = record.dataset.name
    return CaseDetail(
        suite=record.suite.name,
        run_id=record.run_id,
        case_id=case_id,
        case_hash=case_hash,
        content=_content(view.cases.get(case_id), case_hash),
        outputs=[view.redactor.output(o) for o in view.stored.outputs if o.case_id == case_id],
        scores=[view.redactor.score(s) for s in view.stored.scores if s.case_id == case_id],
        annotation=notes.get(dataset, case_id, case_hash),
        older_annotations=notes.older(dataset, case_id, case_hash),
        other_runs=_other_runs(view, case_id, case_hash, candidates),
    )


def _other_runs(
    view: RunView, case_id: str, case_hash: str, candidates: Sequence[RunRow]
) -> list[OtherRun]:
    """Runs that saw the same content of this case. run.json is read first, so runs on other
    content never have their outputs parsed."""
    out: list[OtherRun] = []
    me = (view.record.suite.name, view.record.run_id)
    for row in candidates:
        if (row.suite, row.run_id) == me or row.dataset != view.record.dataset.name:
            continue
        try:
            directory = run_path(view.root, row.suite, row.run_id)
            if read_record(directory).case_hashes.get(case_id) != case_hash:
                continue
            other = load_run(directory, outputs=True)
        except ConfigError:
            continue  # vanished since it was listed
        result = case_results(other.record, other.outputs, other.scores)[case_id]
        out.append(
            OtherRun(
                suite=row.suite,
                run_id=row.run_id,
                config=row.config,
                started_at=row.started_at,
                miss=result.miss,
                scores=result.scores,
            )
        )
        if len(out) == OTHER_RUNS:
            break
    return out


# ---------------------------------------------------------------- one load per request


def run_detail(root: Path, suite: str, run_id: str, redactor: Redactor | None = None) -> RunDetail:
    record = read_record(run_path(root, suite, run_id))
    return RunDetail(
        record=redacted_record(record, redactor or Redactor.from_config(record.redact)),
        baseline=read_baselines(root).get(suite) == run_id,
    )


def case_page(
    root: Path,
    suite: str,
    run_id: str,
    *,
    misses_only: bool = False,
    offset: int = 0,
    limit: int | None = 100,
) -> CasePage:
    items = rows(open_run(root, suite, run_id), Notes.load(root), misses_only=misses_only)
    page, offset, size = window(items, offset, limit)
    return CasePage(items=page, total=len(items), offset=offset, limit=size)


def case_detail(
    root: Path, suite: str, run_id: str, case_id: str, catalog: RunCatalog
) -> CaseDetail:
    view = open_run(root, suite, run_id)
    candidates = catalog.list_runs(RunFilter(dataset=view.record.dataset.name), limit=None)
    return detail(view, case_id, Notes.load(root), candidates.items)


def untriaged(root: Path, suite: str, run_id: str) -> list[tuple[CaseRow, CaseDetail]]:
    """The run's misses with no current annotation for the content the run saw (`triage`),
    built from one load of the run."""
    view = open_run(root, suite, run_id)
    notes = Notes.load(root)
    return [
        (r, detail(view, r.case_id, notes))
        for r in rows(view, notes, misses_only=True)
        if r.annotation is None
    ]


def comparison(
    root: Path,
    baseline: tuple[str, str],
    candidate: tuple[str, str],
    redactor: Redactor | None = None,
) -> Comparison:
    """`compare` of two runs named by (suite, run_id): baseline first. Warnings are redacted
    with both runs' rules unless `redactor` is given."""
    a = load_run(run_path(root, *baseline), outputs=False)
    b = load_run(run_path(root, *candidate), outputs=False)
    return compare_loaded(a, b, redactor)


def compare_loaded(a: StoredRun, b: StoredRun, redactor: Redactor | None = None) -> Comparison:
    """`compare` of two loaded runs, wherever each lives (the export may pair two roots)."""
    c = compare(a.record, a.scores, b.record, b.scores)
    redactor = redactor or union([a.record, b.record])
    warnings = [w.model_copy(update={"message": redactor.text(w.message)}) for w in c.warnings]
    return c.model_copy(update={"warnings": warnings})


def union(records: Sequence[RunRecord], extra: Sequence[str] = ()) -> Redactor:
    """Every rule of every record, plus `extra` built-in rules; one name, one pattern."""
    rules = sorted({r for rec in records for r in rec.redact.rules} | set(extra))
    patterns: dict[str, str] = {}
    for rec in records:
        for name, pattern in rec.redact.patterns.items():
            if patterns.get(name, pattern) != pattern:
                raise ConfigError(f"redaction rule {name!r} has a different pattern in each run")
            patterns[name] = pattern
    return Redactor(rules=tuple(rules), patterns=patterns)


# ---------------------------------------------------------------- datasets


def _summary(root: Path, directory: Path) -> tuple[DatasetSummary, dict[str, str], list[Case]]:
    d = load_dataset(directory)  # the page shows the dataset hash: the full load is needed
    summary = DatasetSummary(
        **d.ref.model_dump(),
        description=d.meta.description,
        license=d.meta.license,
        n_cases=len(d.cases),
        path=directory.relative_to(root).as_posix(),
    )
    return summary, d.case_hashes, list(d.cases)


def datasets(root: Path) -> list[DatasetSummary]:
    out = []
    for meta_path in sorted((root / "datasets").glob("*/dataset.yaml")):
        try:
            out.append(_summary(root, meta_path.parent)[0])
        except ConfigError:
            continue  # a broken dataset is reported by `lite-eval run`, not here
    return out


def dataset_cases(
    root: Path, name: str, *, offset: int = 0, limit: int | None = 100
) -> DatasetCasePage:
    directory = _dataset_dir(root, name)
    if directory is None:
        raise NotFound(f"no dataset {name!r} under {root / 'datasets'}")
    summary, hashes, cases = _summary(root, directory)
    notes = Notes.load(root)
    items = [
        DatasetCase(case=c, case_hash=hashes[c.id], annotation=notes.get(name, c.id, hashes[c.id]))
        for c in cases
    ]
    page, offset, size = window(items, offset, limit)
    return DatasetCasePage(dataset=summary, items=page, total=len(items), offset=offset, limit=size)
