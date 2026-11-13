"""Run directories: the source of truth (design §5.6, §12, §18.7, §18.15).

Write order: outputs.jsonl, scores.jsonl, then run.json through a temporary file and os.replace.
A directory without run.json is a run in progress (or a crashed one) and is never read as a run.
"""

import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from lite_eval.contracts import Output, RunRecord, Score
from lite_eval.errors import ConfigError
from lite_eval.files import atomic_write, read_jsonl


def new_run_id(started_at: datetime, config_name: str) -> str:
    """Design §18.7: <UTC to the microsecond>-<config slug>-<6 random hex>."""
    return f"{started_at:%Y%m%dT%H%M%S%f}Z-{config_name}-{secrets.token_hex(3)}"


def _jsonl(records: Iterable[BaseModel]) -> str:
    return "".join(r.model_dump_json() + "\n" for r in records)


def write_run(
    runs_dir: Path, record: RunRecord, outputs: Sequence[Output], scores: Sequence[Score]
) -> Path:
    directory = runs_dir / record.suite.name / record.run_id
    directory.mkdir(parents=True, exist_ok=False)
    atomic_write(directory / "outputs.jsonl", _jsonl(outputs))
    atomic_write(directory / "scores.jsonl", _jsonl(scores))
    atomic_write(directory / "run.json", record.model_dump_json(indent=2) + "\n")
    return directory


@dataclass(frozen=True)
class StoredRun:
    directory: Path
    record: RunRecord
    outputs: list[Output]  # empty when loaded with outputs=False
    scores: list[Score]

    @property
    def root(self) -> Path:
        return root_of(self.directory)


def root_of(run_directory: Path) -> Path:
    """The evaluation root of a run directory: <root>/runs/<suite>/<run_id>."""
    return run_directory.parents[2]


def load_run(directory: Path, *, outputs: bool = True) -> StoredRun:
    """A finished run. `outputs=False` skips outputs.jsonl (the largest file) for callers that
    need only the record and the scores, such as comparisons and gates."""
    run_json = directory / "run.json"
    if not run_json.is_file():
        raise ConfigError(f"not a finished run directory (no run.json): {directory}")
    try:
        record = RunRecord.model_validate_json(run_json.read_bytes())
    except ValidationError as exc:
        raise ConfigError(f"{run_json}: {exc}") from exc
    stored_outputs = read_jsonl(directory / "outputs.jsonl", Output) if outputs else []
    seen: set[tuple[str, int]] = set()
    for o in stored_outputs:
        if (o.case_id, o.repeat) in seen:
            raise ConfigError(
                f"{directory}: duplicate output for case {o.case_id!r} repeat {o.repeat}"
            )
        seen.add((o.case_id, o.repeat))
    return StoredRun(
        directory=directory,
        record=record,
        outputs=stored_outputs,
        scores=read_jsonl(directory / "scores.jsonl", Score),
    )


# ---------------------------------------------------------------- baselines (design §12)

BASELINES = "baselines.yaml"


def runs_dir(root: Path) -> Path:
    return root / "runs"


def read_baselines(root: Path) -> dict[str, str]:
    """suite name -> run_id of its baseline."""
    path = root / BASELINES
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ConfigError(f"{path}: expected a mapping of suite name to run_id")
    return data


def baseline_run(root: Path, suite: str) -> StoredRun | None:
    run_id = read_baselines(root).get(suite)
    if run_id is None:
        return None
    directory = runs_dir(root) / suite / run_id
    if not directory.is_dir():
        raise ConfigError(f"baseline {run_id} of suite {suite!r} is not in {runs_dir(root)}")
    return load_run(directory, outputs=False)


def set_baseline(stored: StoredRun) -> Path:
    """Make a finished run its suite's baseline; returns the baselines file."""
    root = stored.root
    baselines = read_baselines(root)
    baselines[stored.record.suite.name] = stored.record.run_id
    path = root / BASELINES
    atomic_write(path, yaml.safe_dump(dict(sorted(baselines.items())), sort_keys=False))
    return path
