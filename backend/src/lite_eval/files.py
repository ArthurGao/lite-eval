"""Small file helpers shared by the store, the cache and dataset loading."""

import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ValidationError

from lite_eval.errors import ConfigError


def atomic_write(path: Path, text: str, *, durable: bool = True) -> None:
    """Write through a temporary file and os.replace, so a reader never sees half a file.

    `durable` fsyncs before the rename (run records); the cache can skip it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        if durable:
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp, path)


def read_jsonl[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    """One model per non-blank line; a bad line is a ConfigError naming the file and line."""
    out = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                out.append(model.model_validate_json(line))
            except ValidationError as exc:
                raise ConfigError(f"{path}:{lineno}: {exc}") from exc
    return out
