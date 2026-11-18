"""Canonical JSON and hashing (design §18.6). Every hash in lite-eval goes through here."""

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {_nfc(k): _nfc(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_nfc(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    """NFC-normalised strings, sorted keys, no whitespace, no NaN (raises ValueError)."""
    return json.dumps(
        _nfc(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    """A file's sha256, streamed: a corpus file or a benchmark download may be large."""
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def content_hash(value: Any) -> str:
    """sha256 of the canonical JSON of `value`."""
    return sha256_hex(canonical_json(value))
