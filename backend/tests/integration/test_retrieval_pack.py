"""A retrieval suite end to end: pack defaults, ranking metrics, the ranx cross-check."""

from pathlib import Path
from typing import Any

import pytest

from lite_eval import runner as runner_module
from lite_eval.errors import ConfigError
from lite_eval.runner import RunOptions, RunResult
from lite_eval.targets.fake import FakeTarget, Step
from tests.helpers import execute_suite
from tests.helpers import hit as h

EVIDENCE_CASES = [
    {
        "id": "e1",
        "input": {"query": "ratio"},
        "expected": {"doc": "coffee", "evidence": "1:16"},
        "tags": {"kind": "keyword"},
    },
    {
        "id": "e2",
        "input": {"query": "temp"},
        "expected": {"doc": "coffee", "evidence": "92 degrees"},
        "tags": {"kind": "paraphrase"},
    },
    {
        "id": "e3",
        "input": {"query": "tea"},
        "expected": {"doc": "tea", "evidence": "steep 3 min"},
        "tags": {"kind": "keyword"},
    },
    {
        "id": "e4",
        "input": {"query": "grind"},
        "expected": {"doc": "coffee", "evidence": "medium grind"},
        "tags": {"kind": "keyword"},
    },
]
EVIDENCE_STEPS = {
    "e1": Step(value=[h("coffee", "use 1:16 water")], echo_expected=False),  # answer at 1
    "e2": Step(
        value=[h("tea"), h("coffee", "no"), h("coffee", "about 92  degrees")], echo_expected=False
    ),  # 3, doc 2
    "e3": Step(error="timeout"),  # a failure: counts as nothing retrieved
    "e4": Step(value=[h("coffee", "fine grind")], echo_expected=False),  # doc found, quote not
}

RELEVANCE_CASES = [
    {"id": "r1", "input": "a", "expected": {"relevant": ["d1", "d4"]}},
    {"id": "r2", "input": "b", "expected": {"relevant": ["d9"]}},
]
RELEVANCE_STEPS = {
    "r1": Step(
        value=[h("x", hid="d2"), h("x", hid="d1"), h("x", hid="d3"), h("x", hid="d4")],
        echo_expected=False,
    ),
    "r2": Step(value=[h("x", hid="d5")], echo_expected=False),
}


def run(root: Path, cases: list[dict[str, Any]], steps: dict[str, Step], **spec: Any) -> RunResult:
    target = FakeTarget(steps=steps, config={"case": root.name})
    spec = {"cases": cases, "scorers": [], "pack": "retrieval", **spec}
    (result,) = execute_suite(root, [("a", target)], RunOptions(crosscheck=True), **spec)
    return result


def test_a_retrieval_suite_uses_the_pack_defaults_and_matches_ranx(tmp_path: Path) -> None:
    result = run(tmp_path, EVIDENCE_CASES, EVIDENCE_STEPS)

    m = {k: v.value for k, v in result.record.summary.metrics.items()}
    assert [s.name for s in result.record.scorers] == [
        "hit@1",
        "hit@3",
        "hit@5",
        "mrr@10",
        "doc_hit@5",
    ]
    assert set(m) == {"hit@1", "hit@3", "hit@5", "mrr@10", "doc_hit@5"}  # binary: no pass rates
    assert m["hit@1"] == pytest.approx(1 / 4)
    assert m["hit@3"] == pytest.approx(2 / 4)
    assert m["mrr@10"] == pytest.approx((1 + 1 / 3) / 4)
    assert m["doc_hit@5"] == pytest.approx(3 / 4)
    assert result.record.summary.groups["kind"]["paraphrase"]["hit@1"] == 0.0
    # The check is about ranx: no crosscheck_mismatch. (Was `warnings == []`; e3's timeout is 1 of 4
    # outputs, which since E7 also warns `target_errors`, correctly.)
    codes = [w.code for w in result.record.warnings]
    assert "crosscheck_mismatch" not in codes and codes == ["target_errors"]


def test_relevance_mode_with_repeats_matches_ranx(tmp_path: Path) -> None:
    scorers = ["hit@1", "mrr@10", "ndcg@3", "ndcg@10", "recall@2", "recall@10"]
    result = run(tmp_path, RELEVANCE_CASES, RELEVANCE_STEPS, scorers=scorers, repeats=2)

    m = {k: v.value for k, v in result.record.summary.metrics.items()}
    assert m["mrr@10"] == pytest.approx(0.25)  # r1: first relevant at 2; r2: none
    assert m["recall@10"] == pytest.approx(0.5)
    assert [w.code for w in result.record.warnings] == []


def test_a_gap_to_ranx_is_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "ranx_value", lambda *a: 0.123)  # the boundary: ranx itself

    result = run(tmp_path, EVIDENCE_CASES, EVIDENCE_STEPS)

    codes = [w.code for w in result.record.warnings]
    # Five pack scorers differ from the patched ranx; e3's timeout also warns target_errors (E7).
    assert [c for c in codes if c != "target_errors"] == ["crosscheck_mismatch"] * 5
    assert (
        "hit@1: lite-eval 0.250000000000 vs ranx 0.123000000000"
        in next(w for w in result.record.warnings if w.code == "crosscheck_mismatch").message
    )


def test_crosscheck_without_ranx_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, *a: None if name == "ranx" else real(name, *a)
    )

    with pytest.raises(ConfigError, match=r"\[ranking\] extra"):
        run(tmp_path, EVIDENCE_CASES, EVIDENCE_STEPS)
