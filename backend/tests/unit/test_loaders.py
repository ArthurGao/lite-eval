"""External benchmark loaders: LongMemEval and LoCoMo into dataset directories (design §18.23)."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from lite_eval.cli import app
from lite_eval.datasets.loaders import import_dataset
from lite_eval.datasets.local import load_dataset
from lite_eval.errors import ConfigError

DATA = Path(__file__).parents[1] / "data" / "loaders"
LME = DATA / "longmemeval_sample.json"
LOCOMO = DATA / "locomo_sample.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_longmemeval_questions_become_cases_and_abstention_is_skipped(tmp_path: Path) -> None:
    ds = load_dataset(import_dataset("longmemeval", LME, tmp_path / "lme"))
    assert [c.id for c in ds.cases] == ["q-count-2", "q-pet-1"]
    pet = next(c for c in ds.cases if c.id == "q-pet-1")
    assert pet.input == {
        "question": "What is the name of my cat?",
        "question_date": "2023/05/30 (Tue) 10:00",
        "sessions": [
            {
                "id": "s-a",
                "date": "2023/05/20 (Sat) 09:00",
                "turns": [
                    # has_answer is a label: it never reaches the system under test
                    {"role": "user", "content": "I adopted a cat called Miso last week."},
                    {"role": "assistant", "content": "Congratulations on adopting Miso!"},
                ],
            },
            {
                "id": "s-b",
                "date": "2023/05/21 (Sun) 18:30",
                "turns": [
                    {"role": "user", "content": "Any tips for a vegetable garden?"},
                    {"role": "assistant", "content": "Start with tomatoes and beans."},
                ],
            },
        ],
    }
    assert pet.expected == {"relevant": ["s-a"], "answer": "Miso"}
    assert pet.tags == {"question_type": "single-session-user"}
    count = next(c for c in ds.cases if c.id == "q-count-2")
    assert count.expected == {"relevant": ["s-c", "s-e"], "answer": "2"}  # numbers as text
    meta = ds.meta
    assert (meta.name, meta.pack, meta.license, meta.synthetic) == (
        "longmemeval",
        "memory",
        "MIT",
        False,
    )
    assert meta.version == sha(LME)[:12]
    assert meta.source is not None
    assert (meta.source.name, meta.source.file, meta.source.sha256) == (
        "LongMemEval",
        "longmemeval_sample.json",
        sha(LME),
    )
    assert "skipped 1 abstention question" in meta.description
    assert ds.ref.license == "MIT"  # the license travels into every run record


def test_longmemeval_abstention_kept_on_request_and_limit_counts_in_file_order(
    tmp_path: Path,
) -> None:
    ds = load_dataset(import_dataset("longmemeval", LME, tmp_path / "a", include_abstention=True))
    abst = next(c for c in ds.cases if c.id == "q-job-3_abs")
    assert abst.tags == {"question_type": "single-session-user", "abstention": "true"}
    first = load_dataset(import_dataset("longmemeval", LME, tmp_path / "b", limit=1))
    assert [c.id for c in first.cases] == ["q-pet-1"]
    assert first.meta.version == sha(LME)[:12] + "+first1"
    assert "first 1 question" in first.meta.description


def test_locomo_questions_become_cases(tmp_path: Path) -> None:
    ds = load_dataset(import_dataset("locomo", LOCOMO, tmp_path / "lc"))
    assert [c.id for c in ds.cases] == [
        "conv-x1:q001",
        "conv-x1:q002",
        "conv-x1:q003",
        "conv-x1:q004",
    ]
    by_id = {c.id: c for c in ds.cases}
    first = by_id["conv-x1:q001"]
    # The conversation is stored once in the corpus; every question names it
    assert first.input == {"question": "Where did Rata move to?", "conversation": "conv-x1"}
    assert {c.input["conversation"] for c in ds.cases if isinstance(c.input, dict)} == {"conv-x1"}
    assert sorted(p.name for p in ds.corpus_dir.iterdir()) == ["conv-x1.json"]
    conv = json.loads((ds.corpus_dir / "conv-x1.json").read_text())
    assert (conv["speaker_a"], conv["speaker_b"]) == ("Rata", "Kai")
    sessions = conv["sessions"]
    assert [(s["id"], s["date"]) for s in sessions] == [
        ("session_1", "1:56 pm on 8 May, 2023"),
        ("session_2", "7:10 pm on 20 May, 2023"),
    ]
    assert sessions[0]["turns"][2] == {
        "speaker": "Kai",
        "dia_id": "D1:3",
        "text": "Nice! Still doing the pottery class?",
        "img_url": ["https://example.invalid/p.jpg"],
        "blip_caption": "a photo of a clay pot",
    }
    assert first.expected == {"relevant": ["D1:2"], "answer": "Dunedin"}
    assert first.tags == {"category": "1"}
    assert by_id["conv-x1:q003"].expected == {"relevant": ["D2:2"], "answer": "3"}
    # Category 5: the trap answer is kept apart; there is no right answer to copy
    assert by_id["conv-x1:q004"].expected == {
        "relevant": ["D2:2"],
        "answer": None,
        "adversarial_answer": "celadon",
    }
    assert by_id["conv-x1:q004"].tags == {"category": "5"}
    assert ds.meta.license == "CC BY-NC 4.0"
    assert ds.ref.license == "CC BY-NC 4.0"
    assert ds.meta.source is not None and ds.meta.source.sha256 == sha(LOCOMO)


def test_the_same_file_imports_to_the_same_hash_and_never_overwrites(tmp_path: Path) -> None:
    a = load_dataset(import_dataset("locomo", LOCOMO, tmp_path / "a"))
    b = load_dataset(import_dataset("locomo", LOCOMO, tmp_path / "b", name="locomo"))
    assert a.hash == b.hash
    with pytest.raises(ConfigError, match="already exists"):
        import_dataset("locomo", LOCOMO, tmp_path / "a")


def test_a_malformed_file_is_a_config_error_naming_the_item(tmp_path: Path) -> None:
    bad = json.loads(LME.read_text())
    del bad[1]["answer_session_ids"]
    src = tmp_path / "bad.json"
    src.write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match=r"q-count-2.*answer_session_ids"):
        import_dataset("longmemeval", src, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    with pytest.raises(ConfigError, match="unknown loader"):
        import_dataset("nope", LME, tmp_path / "x")
    with pytest.raises(ConfigError, match="longmemeval only"):
        import_dataset("locomo", LOCOMO, tmp_path / "y", include_abstention=True)


def test_cli_imports_and_refuses_an_existing_directory(tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "datasets" / "lme"
    args = ["dataset", "import", "longmemeval", str(LME), str(out), "--limit", "1"]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert "1 cases" in first.output and "MIT" in first.output
    meta = yaml.safe_load((out / "dataset.yaml").read_text())
    assert meta["license"] == "MIT"
    assert runner.invoke(app, args).exit_code == 2
