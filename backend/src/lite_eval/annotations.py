"""Annotations: a human's triage of one case (design §18.9).

Append-only `<root>/annotations.jsonl`. A correction is a new record whose `supersedes` names
the one it replaces; for each `(dataset, case_id, case_hash)` the current annotation is the one
no record supersedes. Both rules are checked while holding an exclusive lock on
`annotations.jsonl.lock`, the lock the Viewer and `lite-eval triage` share, so two writers can
never fork a chain or create two current records for one key.
"""

import fcntl
import getpass
import os
import secrets
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from lite_eval.contracts import Annotation, AnnotationCategory
from lite_eval.errors import ConfigError
from lite_eval.files import append_jsonl, read_jsonl

ANNOTATIONS = "annotations.jsonl"

Key = tuple[str, str, str]  # (dataset, case_id, case_hash)


class AnnotationConflict(ConfigError):
    """The record it corrects is no longer current, or the key already has a current record:
    reload and correct that one (HTTP 409)."""


def new_annotation_id(now: datetime) -> str:
    """A UUIDv7 (RFC 9562): 48-bit Unix milliseconds, version 7, the variant, 74 random bits.
    Written by hand: `uuid.uuid7` arrives in Python 3.14."""
    value = (int(now.timestamp() * 1000) & (1 << 48) - 1) << 80
    value |= 0x7 << 76 | secrets.randbits(12) << 64 | 0b10 << 62 | secrets.randbits(62)
    h = f"{value:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def default_author() -> str:
    """LITE_EVAL_AUTHOR, else `git config user.name`, else the login name."""
    if author := os.environ.get("LITE_EVAL_AUTHOR", "").strip():
        return author
    try:
        done = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True, timeout=5, check=False
        )
        if done.returncode == 0 and done.stdout.strip():
            return done.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return getpass.getuser()


def read_annotations(root: Path) -> list[Annotation]:
    path = root / ANNOTATIONS
    return read_jsonl(path, Annotation) if path.exists() else []


def current_annotations(annotations: Sequence[Annotation]) -> dict[Key, Annotation]:
    """Key -> the annotation no record supersedes, in file order of the keys."""
    superseded = {a.supersedes for a in annotations if a.supersedes}
    return {a.key: a for a in annotations if a.annotation_id not in superseded}


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    with (root / f"{ANNOTATIONS}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # blocks while another writer holds it
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def append_annotation(root: Path, annotation: Annotation) -> Annotation:
    """Append one record after checking §18.9's rules under the lock; fsync'd before return."""
    with _locked(root):
        existing = read_annotations(root)
        current = current_annotations(existing)
        if annotation.supersedes is None:
            if annotation.key in current:
                raise AnnotationConflict(
                    f"case {annotation.case_id!r} already has a current annotation "
                    f"({current[annotation.key].annotation_id}); correct that one instead"
                )
        else:
            replaced = next(
                (a for a in current.values() if a.annotation_id == annotation.supersedes), None
            )
            if replaced is None:
                raise AnnotationConflict(
                    f"annotation {annotation.supersedes} is no longer current; reload and "
                    "correct the current one"
                )
            if replaced.key != annotation.key:
                raise AnnotationConflict(
                    f"annotation {annotation.supersedes} belongs to another case or content"
                )
        append_jsonl(root / ANNOTATIONS, annotation)
    return annotation


def annotate(
    root: Path,
    *,
    dataset: str,
    case_id: str,
    case_hash: str,
    category: AnnotationCategory,
    note: str = "",
    run_id: str | None = None,
    supersedes: str | None = None,
    author: str | None = None,
    now: datetime | None = None,
) -> Annotation:
    """Write one annotation; the id, author and time are filled here (§18.9), for the Viewer and
    `lite-eval triage` alike. A note over 4 KB is a ConfigError."""
    now = now or datetime.now(UTC)
    try:
        annotation = Annotation(
            annotation_id=new_annotation_id(now),
            dataset=dataset,
            case_id=case_id,
            case_hash=case_hash,
            run_id=run_id,
            category=category,
            note=note,
            author=author or default_author(),
            created_at=now,
            supersedes=supersedes,
        )
    except ValidationError as exc:
        raise ConfigError(f"invalid annotation: {exc.errors()[0]['msg']}") from exc
    return append_annotation(root, annotation)
