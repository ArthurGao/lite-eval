"""KB export (design §13, §18.24): run records as rows of the KB's llm-evals `results.jsonl`,
checked against the KB README schema before they are written."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lite_eval.errors import ConfigError
from lite_eval.report.kb import kb_rows, render_rows
from lite_eval.store import read_record
from lite_eval.suite import KbSpec

V1_RUN = Path(__file__).parents[1] / "data" / "records" / "v1" / "run"
EXPECTED = Path(__file__).parents[1] / "data" / "kb" / "v1-run.jsonl"
KB = Path.home() / "Docs" / "Knowledgebase" / "global" / "llm-evals"

SPEC: dict[str, Any] = {
    "eval": "2026-11-13-lite-eval-sample",
    "project": "lite-eval",
    "task": "judging",
    "dataset": "three synthetic capital and arithmetic questions",
    "grader": "labels",
    "vendor": "fake",
    "model": "fake-echo",
    "effort": "n/a",
    "metrics": {"exact_match": "accuracy"},
    "notes": "committed sample run: the export's fixed expected output",
}


def test_the_sample_run_exports_exactly_the_committed_rows() -> None:
    rows = kb_rows([read_record(V1_RUN)], KbSpec.model_validate(SPEC))
    assert render_rows(rows) == EXPECTED.read_text(encoding="utf-8")
    (row,) = rows
    assert (row["value"], row["n"], row["trials"], row["date"]) == (0.6667, 3, 1, "2026-11-13")
    assert row["variant"] == "plain" and "values" not in row and "cost_usd" not in row


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"task": "poetry"}, "task"),
        ({"effort": "extreme"}, "effort"),
        ({"grader": "vibes"}, "grader"),
    ],
)
def test_a_kb_section_the_kb_would_reject_fails_when_the_suite_loads(
    change: dict[str, Any], field: str
) -> None:
    with pytest.raises(ValidationError) as refused:
        KbSpec.model_validate({**SPEC, **change})
    assert [e["loc"] for e in refused.value.errors()] == [(field,)]


def test_a_row_the_kb_would_reject_is_refused_at_export() -> None:
    record = read_record(V1_RUN)
    target = record.target.model_copy(update={"config": {**record.target.config, "effort": "x"}})
    derived = record.model_copy(update={"target": target})
    spec = KbSpec.model_validate({k: v for k, v in SPEC.items() if k != "effort"})
    with pytest.raises(ConfigError, match=r"effort 'x' is not one of"):
        kb_rows([derived], spec)
    with pytest.raises(ConfigError, match="missing vendor"):
        kb_rows([record], KbSpec.model_validate({**SPEC, "vendor": None}))
    with pytest.raises(ConfigError, match="no metric 'recall@5'"):
        kb_rows([record], KbSpec.model_validate({**SPEC, "metrics": {"recall@5": "recall"}}))


@pytest.mark.kb
def test_the_rows_pass_the_kb_s_own_check(tmp_path: Path) -> None:
    """Opt-in (`-m kb`): needs the owner's KB and node. The rows are appended to a copy of the
    llm-evals directory, with a card for their eval, and its check.mjs must pass."""
    shutil.copytree(KB.parent, tmp_path / "global")  # pages link to their siblings in global/
    copy = tmp_path / "global" / "llm-evals"
    rows = kb_rows([read_record(V1_RUN)], KbSpec.model_validate(SPEC))
    with (copy / "results.jsonl").open("a", encoding="utf-8") as f:
        f.write(render_rows(rows))
    (copy / "evals" / f"{SPEC['eval']}.md").write_text("# sample card\n", encoding="utf-8")
    done = subprocess.run(
        ["node", str(copy / "scripts" / "check.mjs")], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout
    assert ", 0 error(s)." in done.stdout
    assert json.loads(render_rows(rows).splitlines()[0])["eval"] == SPEC["eval"]


def test_the_row_date_is_the_start_date_in_the_suite_s_timezone() -> None:
    """A run started on a New Zealand morning is the previous day in UTC: the KB dates a
    measurement by the owner's day, so the suite names its timezone (default UTC, which
    keeps every existing export unchanged)."""
    from datetime import UTC, datetime

    record = read_record(V1_RUN)
    late = record.model_copy(update={"started_at": datetime(2026, 11, 17, 23, 18, tzinfo=UTC)})
    assert kb_rows([late], KbSpec.model_validate(SPEC))[0]["date"] == "2026-11-17"
    nz = KbSpec.model_validate({**SPEC, "timezone": "Pacific/Auckland"})
    assert kb_rows([late], nz)[0]["date"] == "2026-11-18"
    assert kb_rows([record], nz)[0]["date"] == "2026-11-13"  # 09:00Z is 22:00 there


def test_an_unknown_timezone_fails_when_the_suite_loads() -> None:
    with pytest.raises(ValidationError, match="unknown timezone 'Mars/Olympus'"):
        KbSpec.model_validate({**SPEC, "timezone": "Mars/Olympus"})
