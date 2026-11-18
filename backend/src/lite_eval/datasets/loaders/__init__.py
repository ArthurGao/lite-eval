"""External benchmarks imported into ordinary dataset directories (design §18.23).

`lite-eval dataset import <loader> <file> <out>` reads an official benchmark file and writes
`dataset.yaml` + `cases.jsonl` (+ `corpus/` when the benchmark shares content between cases), so
hashing, the secret scan, `rescore` and the Viewer treat it like any other dataset. The file's
sha256 and license are recorded in `dataset.yaml`; the license also travels into every run record
(`DatasetRef.license`). The file is read and written one item at a time (`iter_json_list`): parsed
whole, LongMemEval M (2.7 GB) would take ~24 GB of memory (S, 277 MB, measured 2.4 GB).
"""

import json
import shutil
import tempfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, TextIO

import yaml

from lite_eval.canonical import file_sha256
from lite_eval.datasets.loaders import locomo, longmemeval
from lite_eval.datasets.loaders._common import Item, Source
from lite_eval.datasets.local import CORPUS, DatasetMeta, DatasetSource
from lite_eval.errors import ConfigError

SOURCES: dict[str, Source] = {"longmemeval": longmemeval.SOURCE, "locomo": locomo.SOURCE}
CHUNK = 1 << 20  # characters read at a time
_SPACE = " \t\n\r"
_NUMBER = "0123456789.eE+-"


def iter_json_list(f: TextIO, chunk: int = CHUNK) -> Iterator[Any]:
    """The items of the JSON list in `f`, one at a time; only the item being parsed is in memory.

    A value cut by the end of the buffer fails to parse (or, for a number, parses short): more is
    read, at least as much as the buffer holds, and the item parsed again from its start, so an
    item costs at most about twice its size in parsing (an M item is ~5 MB). A ConfigError names
    the item where the file stops being a JSON list.
    """
    decoder = json.JSONDecoder()
    buf, pos, eof = "", 0, False

    def more(size: int = chunk) -> bool:
        nonlocal buf, pos, eof
        if not eof:
            text = f.read(size)
            eof = not text
            buf, pos = buf[pos:] + text, 0
        return not eof

    def peek() -> str | None:
        """The next character that is not white space (None at the end of the file)."""
        nonlocal pos
        while True:
            while pos < len(buf) and buf[pos] in _SPACE:
                pos += 1
            if pos < len(buf):
                return buf[pos]
            if not more():
                return None

    if peek() != "[":
        raise ConfigError("expected a JSON list")
    pos += 1
    if peek() == "]":
        pos += 1
    else:
        n = 0
        while True:
            n += 1
            peek()  # past white space; at the end of the file the parse below says so
            while True:
                try:
                    value, end = decoder.raw_decode(buf, pos)
                except json.JSONDecodeError as exc:
                    if more(max(chunk, len(buf) - pos)):  # the buffer at least doubles
                        continue
                    if exc.pos >= len(buf):
                        raise ConfigError(f"the file ends inside item {n}") from None
                    raise ConfigError(f"item {n}: {exc.msg}") from None
                # A number cut by the buffer parses short ("3" of "3.5"): it is complete only
                # when followed by a character that cannot continue it.
                if (end == len(buf) or buf[end] in _NUMBER) and more():
                    continue
                break
            pos = end
            yield value
            following = peek()
            if following == ",":
                pos += 1
            elif following == "]":
                pos += 1
                break
            elif following is None:
                raise ConfigError(f"the file ends after item {n}")
            else:
                raise ConfigError(f"expected ',' or ']' after item {n}")
    if peek() is not None:
        raise ConfigError("unexpected text after the list")


def _read(
    loader: str,
    items: Iterable[Any],
    limit: int | None,
    include_abstention: bool,
    notes: list[str],
) -> Iterator[Item]:
    if loader == "longmemeval":
        return longmemeval.read(items, limit, include_abstention, notes)
    return locomo.read(items, limit)


def _write(out: Path, items: Iterator[Item], meta_for: Callable[[int], DatasetMeta]) -> None:
    """Written item by item beside the target, the metadata last (it says what was left out),
    then renamed into place: a failed import leaves nothing behind."""
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    try:
        count = 0
        with (staging / "cases.jsonl").open("w", encoding="utf-8") as f:
            for item in items:
                f.write(item.case.model_dump_json(exclude_defaults=True) + "\n")
                count += 1
                for name, content in item.corpus.items():
                    (staging / CORPUS).mkdir(exist_ok=True)
                    (staging / CORPUS / f"{name}.json").write_text(
                        json.dumps(content, ensure_ascii=False), encoding="utf-8"
                    )
        meta = meta_for(count)
        (staging / "dataset.yaml").write_text(
            yaml.safe_dump(meta.model_dump(mode="json", exclude_defaults=True), allow_unicode=True),
            encoding="utf-8",
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
    if include_abstention and loader != "longmemeval":
        raise ConfigError("--include-abstention applies to longmemeval only")
    # The reader appends what it left out once it has read every item; `_write` calls
    # `meta_for` only then, so the description is complete.
    notes = [f"first {limit} question(s) of the file"] if limit is not None else []

    def meta_for(count: int) -> DatasetMeta:
        if not count:
            raise ConfigError("no cases")
        return DatasetMeta(
            name=name or loader,
            version=digest[:12] + (f"+first{limit}" if limit is not None else ""),
            pack="memory",
            description="; ".join([f"imported from {source.name} {src.name}", *notes]),
            license=source.license,
            source=DatasetSource(name=source.name, url=source.url, file=src.name, sha256=digest),
        )

    try:
        digest = file_sha256(src)  # a pass of its own: the raw bytes are not kept
        with src.open(encoding="utf-8") as f:
            items = _read(loader, iter_json_list(f), limit, include_abstention, notes)
            _write(out, items, meta_for)
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {src}: {exc}") from exc
    except ConfigError as exc:  # the file's problem: say which file
        raise ConfigError(f"{src}: {exc}") from None
    return out
