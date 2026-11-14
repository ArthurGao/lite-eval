"""Contracts round-trip; records of an earlier schema_version still load (AGENTS rule 1)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from lite_eval.contracts import Output, RunRecord, Score
from lite_eval.packs.cost import nearest_rank
from lite_eval.store import load_run

V1_RUN = Path(__file__).parents[1] / "data" / "records" / "v1" / "run"


def test_a_committed_v1_run_directory_still_loads() -> None:
    stored = load_run(V1_RUN)

    assert stored.record.schema_version == 1
    assert stored.record.run_id == "20261113T090000000000Z-plain-a1b2c3"
    assert stored.record.summary.metrics["exact_match"].value == pytest.approx(2 / 3)
    assert [o.case_id for o in stored.outputs] == ["q1", "q2", "q3"]
    assert stored.outputs[2].error is not None and stored.outputs[2].error.kind == "timeout"
    assert [s.passed for s in stored.scores] == [True, True, False]


def test_records_round_trip_through_json() -> None:
    stored = load_run(V1_RUN)

    assert RunRecord.model_validate_json(stored.record.model_dump_json()) == stored.record
    for o in stored.outputs:
        assert Output.model_validate_json(o.model_dump_json()) == o


def test_records_are_frozen_and_reject_unknown_fields() -> None:
    out = Output(case_id="q1", value="x")

    with pytest.raises(ValidationError):
        out.value = "y"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Output.model_validate({"case_id": "q1", "valu": "x"})


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_a_score_value_outside_0_1_is_refused(value: float) -> None:
    with pytest.raises(ValidationError):
        Score(case_id="q1", scorer="s", scorer_version="1", value=value)


def test_p95_is_the_nearest_rank_without_interpolation() -> None:
    values = [float(v) for v in range(1, 21)]  # 1..20: ceil(0.95 * 20) = 19th value

    assert nearest_rank(values, 0.95) == 19.0
    assert nearest_rank([5.0], 0.95) == 5.0
    assert nearest_rank([3.0, 1.0, 2.0], 0.5) == 2.0  # ceil(1.5) = 2nd


def test_e4d_fields_default_on_records_written_before_them() -> None:
    """E4d only adds fields: `redact.known` (names of secrets masked by value) and a gate rule's
    `configs`; a v1 record and a rule written without them load with the defaults."""
    from lite_eval.contracts import GateRule

    stored = load_run(V1_RUN)
    assert stored.record.redact.known == []
    rule = GateRule.model_validate({"metric": "exact_match", "min": 0.9})
    assert rule.configs is None and rule.applies_to("any-config")
    assert not GateRule(metric="exact_match", min=0.9, configs=["plain"]).applies_to("rerank")
