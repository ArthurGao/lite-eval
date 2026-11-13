import hashlib
import math
from pathlib import Path

import pytest

from lite_eval.canonical import canonical_json, content_hash
from lite_eval.contracts import Case
from lite_eval.datasets.local import load_dataset
from lite_eval.errors import ConfigError
from tests.helpers import write_dataset

# "é" written decomposed (e + combining acute); canonical JSON must give the NFC form U+00E9.
DECOMPOSED_E = "é"


def test_canonical_json_sorts_keys_compacts_and_normalises_nfc() -> None:
    text = canonical_json({"b": [1, 2.5], "a": {"q": DECOMPOSED_E, DECOMPOSED_E: None}})

    assert text == '{"a":{"q":"é","é":null},"b":[1,2.5]}'


def test_canonical_json_refuses_nan() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": math.nan})


def test_case_hash_covers_id_input_expected_tags_but_not_provenance() -> None:
    case = Case(id="q1", input={"query": DECOMPOSED_E}, tags={"kind": "f"})
    by_hand = hashlib.sha256(
        '{"expected":null,"id":"q1","input":{"query":"é"},"tags":{"kind":"f"}}'.encode()
    ).hexdigest()

    assert case.content_hash() == by_hand
    with_provenance = Case(
        id="q1", input={"query": "é"}, tags={"kind": "f"}, provenance={"author": "x"}
    )
    assert with_provenance.content_hash() == by_hand
    assert Case(id="q1", input={"query": "e"}, tags={"kind": "f"}).content_hash() != by_hand


def test_dataset_hash_follows_the_byte_stream_of_design_18_6(tmp_path: Path) -> None:
    root = write_dataset(
        tmp_path / "ds",
        [{"id": "q2", "input": "b"}, {"id": "q1", "input": "a"}],
        corpus={"b/z.txt": b"zz", "a.txt": b"hello"},
    )

    case_hash = {
        cid: hashlib.sha256(
            f'{{"expected":null,"id":"{cid}","input":"{inp}","tags":{{}}}}'.encode()
        ).hexdigest()
        for cid, inp in (("q1", "a"), ("q2", "b"))
    }
    stream = (
        b"lite-eval-dataset-v1\n"
        + b'{"name":"demo","pack":null,"version":"1"}'
        + case_hash["q1"].encode()
        + case_hash["q2"].encode()
        + b"a.txt\0"
        + hashlib.sha256(b"hello").hexdigest().encode()
        + b"b/z.txt\0"
        + hashlib.sha256(b"zz").hexdigest().encode()
    )

    dataset = load_dataset(root)

    assert dataset.hash == hashlib.sha256(stream).hexdigest()
    assert [c.id for c in dataset.cases] == ["q1", "q2"]
    assert dataset.case_hashes == case_hash


def test_dataset_hash_changes_with_a_corpus_byte_and_ignores_mtime(tmp_path: Path) -> None:
    root = write_dataset(tmp_path / "ds", [{"id": "q1", "input": "a"}], corpus={"a.txt": b"hello"})
    before = load_dataset(root).hash

    (root / "corpus" / "a.txt").touch()
    assert load_dataset(root).hash == before
    (root / "corpus" / "a.txt").write_bytes(b"hellO")
    assert load_dataset(root).hash != before


def test_duplicate_case_ids_are_refused(tmp_path: Path) -> None:
    root = write_dataset(tmp_path / "ds", [{"id": "q1", "input": "a"}, {"id": "q1", "input": "b"}])

    with pytest.raises(ConfigError, match="duplicate case id 'q1'"):
        load_dataset(root)


def test_symlinks_in_the_corpus_are_refused(tmp_path: Path) -> None:
    root = write_dataset(tmp_path / "ds", [{"id": "q1", "input": "a"}], corpus={"a.txt": b"x"})
    (root / "corpus" / "link.txt").symlink_to(root / "corpus" / "a.txt")

    with pytest.raises(ConfigError, match="symbolic links"):
        load_dataset(root)


SECRET = "sk-abcdefghijklmnopqrstuvwx"


def test_a_secret_looking_value_stops_the_load(tmp_path: Path) -> None:
    root = write_dataset(tmp_path / "ds", [{"id": "q1", "input": f"use {SECRET} please"}])

    with pytest.raises(ConfigError, match="case 'q1': rule api_keys") as info:
        load_dataset(root)
    assert SECRET not in str(info.value)


def test_a_false_positive_is_allowed_per_match_and_reported(tmp_path: Path) -> None:
    allow = {
        "case_id": "q1",
        "rule": "api_keys",
        "match_sha256": hashlib.sha256(SECRET.encode()).hexdigest(),
        "reason": "a made-up key in a test question",
    }
    stale = {**allow, "case_id": "q9"}
    root = write_dataset(
        tmp_path / "ds",
        [{"id": "q1", "input": f"use {SECRET} please"}],
        secret_scan_allow=[allow, stale],
    )

    dataset = load_dataset(root)

    assert [(w.code, "q1" in w.message or "q9" in w.message) for w in dataset.warnings] == [
        ("secret_scan_allowed", True),
        ("secret_scan_allow_unused", True),
    ]
    assert "q9" in dataset.warnings[1].message


def test_unknown_dataset_fields_are_refused(tmp_path: Path) -> None:
    root = write_dataset(tmp_path / "ds", [{"id": "q1", "input": "a"}], contains_secrets="ok")

    with pytest.raises(ConfigError, match="contains_secrets"):
        load_dataset(root)


def test_content_hash_is_sha256_of_canonical_json() -> None:
    assert content_hash({"a": 1}) == hashlib.sha256(b'{"a":1}').hexdigest()
