"""Annotations (design §18.9): append-only, one current record per key, written under a lock."""

import fcntl
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lite_eval.annotations import (
    ANNOTATIONS,
    AnnotationConflict,
    append_annotation,
    current_annotations,
    default_author,
    new_annotation_id,
    read_annotations,
)
from lite_eval.contracts import Annotation, AnnotationCategory

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


def note(
    case_id: str = "q1",
    category: AnnotationCategory = "real_miss",
    supersedes: str | None = None,
    case_hash: str = "h1",
    text: str = "",
) -> Annotation:
    return Annotation(
        annotation_id=new_annotation_id(NOW),
        dataset="golden",
        case_id=case_id,
        case_hash=case_hash,
        category=category,
        note=text,
        author="tester",
        created_at=NOW,
        supersedes=supersedes,
    )


def test_a_correction_supersedes_the_current_record(tmp_path: Path) -> None:
    first = append_annotation(tmp_path, note())
    second = append_annotation(
        tmp_path, note(category="label_wrong", supersedes=first.annotation_id)
    )
    third = append_annotation(tmp_path, note(category="unclear", supersedes=second.annotation_id))

    stored = read_annotations(tmp_path)
    assert [a.annotation_id for a in stored] == [
        first.annotation_id,
        second.annotation_id,
        third.annotation_id,
    ]
    current = current_annotations(stored)
    assert list(current) == [("golden", "q1", "h1")]
    assert current["golden", "q1", "h1"].category == "unclear"


def test_correcting_a_superseded_record_is_a_conflict(tmp_path: Path) -> None:
    first = append_annotation(tmp_path, note())
    append_annotation(tmp_path, note(category="label_wrong", supersedes=first.annotation_id))

    with pytest.raises(AnnotationConflict, match="no longer current"):
        append_annotation(tmp_path, note(category="excluded", supersedes=first.annotation_id))
    assert len(read_annotations(tmp_path)) == 2  # nothing written: no forked chain


def test_a_new_record_for_a_key_that_has_one_is_a_conflict(tmp_path: Path) -> None:
    append_annotation(tmp_path, note())

    with pytest.raises(AnnotationConflict, match="already has a current annotation"):
        append_annotation(tmp_path, note(category="excluded"))
    # another case, or the same case with new content, is a separate key
    append_annotation(tmp_path, note(case_id="q2"))
    append_annotation(tmp_path, note(case_hash="h2"))
    assert len(current_annotations(read_annotations(tmp_path))) == 3


def test_a_correction_must_keep_its_key(tmp_path: Path) -> None:
    first = append_annotation(tmp_path, note())

    with pytest.raises(AnnotationConflict, match="another case"):
        append_annotation(tmp_path, note(case_id="q2", supersedes=first.annotation_id))
    with pytest.raises(AnnotationConflict, match="no longer current"):
        append_annotation(tmp_path, note(supersedes="0191f5a0-0000-7000-8000-000000000000"))


def test_no_annotations_file_reads_as_none(tmp_path: Path) -> None:
    assert read_annotations(tmp_path) == []


_APPEND_ONE = """
import sys
from datetime import UTC, datetime
from pathlib import Path
from lite_eval.annotations import append_annotation, new_annotation_id
from lite_eval.contracts import Annotation
now = datetime(2026, 11, 14, tzinfo=UTC)
append_annotation(Path(sys.argv[1]), Annotation(
    annotation_id=new_annotation_id(now), dataset="golden", case_id="q9", case_hash="h",
    category="real_miss", author="other process", created_at=now))
print("written")
"""


def test_a_writer_waits_for_the_lock_another_process_holds(tmp_path: Path) -> None:
    lock = (tmp_path / f"{ANNOTATIONS}.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    writer = subprocess.Popen(
        [sys.executable, "-c", _APPEND_ONE, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    try:
        time.sleep(1.0)
        assert writer.poll() is None  # blocked on the lock
        assert read_annotations(tmp_path) == []
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    out, _ = writer.communicate(timeout=30)
    assert writer.returncode == 0 and out.strip() == "written"
    assert [a.author for a in read_annotations(tmp_path)] == ["other process"]


def test_a_note_is_limited_to_4_kb_of_utf8() -> None:
    assert len(note(text="x" * 4096).note) == 4096
    assert len(note(text="é" * 2048).note) == 2048  # 4096 bytes
    with pytest.raises(ValidationError, match="4096 bytes"):
        note(text="é" * 2049)  # 2049 characters, 4098 bytes


def test_annotation_ids_are_uuid7_and_sort_by_time() -> None:
    early = new_annotation_id(datetime(2026, 1, 1, tzinfo=UTC))
    late = new_annotation_id(datetime(2026, 11, 14, tzinfo=UTC))
    assert early < late
    assert early[14] == "7" and late[19] in "89ab"  # version 7, RFC 9562 variant
    assert early[:13] == "019b76da-a800"  # 1767225600000 ms = 0x19b76daa800


def test_the_author_comes_from_the_environment_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITE_EVAL_AUTHOR", "reviewer-1")
    assert default_author() == "reviewer-1"
    monkeypatch.delenv("LITE_EVAL_AUTHOR")
    assert default_author()  # git user.name or the login name; never empty
