"""External benchmarks imported into ordinary dataset directories (design §18.23).

`lite-eval dataset import <loader> <file> <out>` reads an official benchmark file and writes
`dataset.yaml` + `cases.jsonl` (+ `corpus/` when the benchmark shares content between cases), so
hashing, the secret scan, `rescore` and the Viewer treat it like any other dataset. The file's
sha256 and license are recorded in `dataset.yaml`; the license also travels into every run record
(`DatasetRef.license`).
"""

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from lite_eval.datasets.loaders import locomo, longmemeval
from lite_eval.datasets.loaders._common import Loaded, Source
from lite_eval.datasets.local import CORPUS, DatasetMeta, DatasetSource
from lite_eval.errors import ConfigError

SOURCES: dict[str, Source] = {"longmemeval": longmemeval.SOURCE, "locomo": locomo.SOURCE}


def _read(loader: str, parsed: list[Any], limit: int | None, include_abstention: bool) -> Loaded:
    if loader == "longmemeval":
        return longmemeval.read(parsed, limit, include_abstention)
    if include_abstention:
        raise ConfigError("--include-abstention applies to longmemeval only")
    return locomo.read(parsed, limit)


def _write(out: Path, meta: DatasetMeta, loaded: Loaded) -> None:
    """Written beside the target and renamed into place: a failed import leaves nothing behind."""
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    try:
        (staging / "dataset.yaml").write_text(
            yaml.safe_dump(meta.model_dump(mode="json", exclude_defaults=True), allow_unicode=True),
            encoding="utf-8",
        )
        with (staging / "cases.jsonl").open("w", encoding="utf-8") as f:
            for case in loaded.cases:
                f.write(case.model_dump_json(exclude_defaults=True) + "\n")
        if loaded.corpus:
            (staging / CORPUS).mkdir()
            for name, content in loaded.corpus.items():
                (staging / CORPUS / f"{name}.json").write_text(
                    json.dumps(content, ensure_ascii=False), encoding="utf-8"
                )
        staging.rename(out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def import_dataset(
    loader: str,
    src: Path,
    out: Path,
    *,
    name: str | None = None,
    limit: int | None = None,
    include_abstention: bool = False,
) -> Path:
    if loader not in SOURCES:
        raise ConfigError(f"unknown loader {loader!r}; available: {sorted(SOURCES)}")
    source = SOURCES[loader]
    if out.exists():
        raise ConfigError(f"{out} already exists; import into a new directory")
    if limit is not None and limit < 1:
        raise ConfigError("--limit must be at least 1")
    try:
        with src.open("rb") as f:  # hashed and parsed from the file: the raw bytes are not kept
            digest = hashlib.file_digest(f, "sha256").hexdigest()
            f.seek(0)
            parsed = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read {src}: {exc}") from exc
    if not isinstance(parsed, list):
        raise ConfigError(f"{src}: expected a JSON list, as in the official {source.name} file")
    loaded = _read(loader, parsed, limit, include_abstention)
    del parsed  # the cases hold what is kept
    if not loaded.cases:
        raise ConfigError(f"{src}: no cases")
    notes = [f"first {limit} question(s) of the file"] if limit is not None else []
    meta = DatasetMeta(
        name=name or loader,
        version=digest[:12] + (f"+first{limit}" if limit is not None else ""),
        pack="memory",
        description="; ".join([f"imported from {source.name} {src.name}", *notes, *loaded.notes]),
        license=source.license,
        source=DatasetSource(name=source.name, url=source.url, file=src.name, sha256=digest),
    )
    _write(out, meta, loaded)
    return out
