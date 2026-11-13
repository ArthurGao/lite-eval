"""Run directories: the source of truth (design §5.6, §12, §18.7, §18.15).

Write order: outputs.jsonl, scores.jsonl, then run.json through a temporary file and os.replace.
A directory without run.json is a run in progress (or a crashed one) and is never read as a run.
"""

import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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
    outputs: list[Output]
    scores: list[Score]


def load_run(directory: Path) -> StoredRun:
    run_json = directory / "run.json"
    if not run_json.is_file():
        raise ConfigError(f"not a finished run directory (no run.json): {directory}")
    try:
        record = RunRecord.model_validate_json(run_json.read_bytes())
    except ValidationError as exc:
        raise ConfigError(f"{run_json}: {exc}") from exc
    outputs = read_jsonl(directory / "outputs.jsonl", Output)
    seen: set[tuple[str, int]] = set()
    for o in outputs:
        if (o.case_id, o.repeat) in seen:
            raise ConfigError(
                f"{directory}: duplicate output for case {o.case_id!r} repeat {o.repeat}"
            )
        seen.add((o.case_id, o.repeat))
    return StoredRun(
        directory=directory,
        record=record,
        outputs=outputs,
        scores=read_jsonl(directory / "scores.jsonl", Score),
    )
