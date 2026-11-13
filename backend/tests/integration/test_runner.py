"""The two-phase runner against FakeTarget (design §6, §16 E1 acceptance)."""

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from lite_eval.contracts import Case, Output, Span, Usage
from lite_eval.datasets.local import load_dataset
from lite_eval.errors import ConfigError
from lite_eval.runner import RunOptions, RunResult, backoff_s, execute
from lite_eval.store import load_run
from lite_eval.suite import load_suite
from lite_eval.targets.base import Target
from lite_eval.targets.fake import FakeTarget, Step
from tests.helpers import DEFAULT_CASES, write_suite


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def run(
    root: Path,
    targets: Sequence[tuple[str, Target]],
    options: RunOptions | None = None,
    **suite_spec: Any,
) -> list[RunResult]:
    suite_path = root / "suites" / "s.yaml"
    if suite_spec or not suite_path.exists():
        write_suite(root, **suite_spec)
    suite = load_suite(suite_path)
    dataset = load_dataset(suite.dataset_path)
    return asyncio.run(execute(suite, dataset, targets, options or RunOptions(sleep=Sleeps())))


def scores_of(result: RunResult) -> list[tuple[str, int, str, float, bool | None]]:
    stored = load_run(result.directory)
    return [(s.case_id, s.repeat, s.scorer, s.value, s.passed) for s in stored.scores]


# ---------------------------------------------------------------- the basic path


def test_a_run_writes_an_immutable_record_with_its_provenance(tmp_path: Path) -> None:
    target = FakeTarget(versions={"engine": "2.1"})
    (result,) = run(tmp_path, [("a", target)])

    stored = load_run(result.directory)
    rec = stored.record
    assert result.directory == tmp_path / "runs" / "demo-suite" / rec.run_id
    assert sorted(p.name for p in result.directory.iterdir()) == [
        "outputs.jsonl",
        "run.json",
        "scores.jsonl",
    ]
    assert rec.status == "complete"
    assert rec.target.config == {"name": "a", "mode": "echo"}
    assert rec.target.versions == {"engine": "2.1"}
    assert rec.dataset.hash == load_dataset(tmp_path / "datasets" / "demo").hash
    assert rec.suite.path == "suites/s.yaml"  # relative: no absolute local path in records
    assert [s.name for s in rec.scorers] == ["exact_match"]
    assert rec.summary.metrics["exact_match"].value == 1.0
    assert rec.summary.metrics["exact_match:pass_rate"].value == 1.0
    assert rec.summary.outputs_expected == 3
    assert rec.summary.case_weight == pytest.approx(1 / 3)
    assert rec.summary.groups["kind"] == {
        "fact": {"exact_match": 1.0, "exact_match:pass_rate": 1.0},
        "math": {"exact_match": 1.0, "exact_match:pass_rate": 1.0},
    }
    assert [o.case_id for o in stored.outputs] == ["q1", "q2", "q3"]
    assert (target.setups, target.teardowns) == (1, 1)


# ---------------------------------------------------------------- cache and rescore


def test_the_second_run_hits_the_cache_for_every_output_with_identical_scores(
    tmp_path: Path,
) -> None:
    (first,) = run(tmp_path, [("a", FakeTarget())])
    again = FakeTarget()
    (second,) = run(tmp_path, [("a", again)])

    assert sum(again.calls.values()) == 0
    assert (second.record.cache.hits, second.record.cache.misses) == (3, 0)
    assert scores_of(second) == scores_of(first)
    assert load_run(second.directory).outputs == load_run(first.directory).outputs


@pytest.mark.parametrize(
    "change",
    ["versions", "config", "setup_identity"],
)
def test_anything_that_decides_the_output_invalidates_the_cache(
    tmp_path: Path, change: str
) -> None:
    run(tmp_path, [("a", FakeTarget())])
    changes: dict[str, dict[str, Any]] = {
        "versions": {"versions": {"fake": "2"}},
        "config": {"config": {"mode": "echo", "temperature": 0.2}},
        "setup_identity": {"setup_identity": {"index": "bge-m3"}},
    }
    kwargs = changes[change]
    target = FakeTarget(**kwargs)
    run(tmp_path, [("a", target)])

    assert sum(target.calls.values()) == 3


def test_a_changed_case_regenerates_every_output_because_the_dataset_hash_changed(
    tmp_path: Path,
) -> None:
    run(tmp_path, [("a", FakeTarget())])
    cases = [dict(c) for c in DEFAULT_CASES]
    cases[1]["expected"] = "four"
    target = FakeTarget()
    run(tmp_path, [("a", target)], cases=cases)

    assert sum(target.calls.values()) == 3


def test_a_corrupt_cache_entry_is_a_miss_with_a_warning(tmp_path: Path) -> None:
    run(tmp_path, [("a", FakeTarget())])
    entry = next((tmp_path / ".lite-eval" / "cache").rglob("*.json"))
    entry.write_text("{not json")
    target = FakeTarget()
    (result,) = run(tmp_path, [("a", target)])

    assert sum(target.calls.values()) == 1
    assert [w.code for w in result.record.warnings] == ["cache_corrupt"]


def test_no_cache_regenerates(tmp_path: Path) -> None:
    run(tmp_path, [("a", FakeTarget())])
    target = FakeTarget()
    run(tmp_path, [("a", target)], RunOptions(no_cache=True, sleep=Sleeps()))

    assert sum(target.calls.values()) == 3


def test_errored_outputs_are_not_cached(tmp_path: Path) -> None:
    run(tmp_path, [("a", FakeTarget(steps={"q2": Step(error="truncated")}))])
    target = FakeTarget()
    run(tmp_path, [("a", target)])

    assert dict(target.calls) == {"q2": 1}


# ---------------------------------------------------------------- failures count (design §18.8)


class _ReturnsNonsense(FakeTarget):
    async def run(self, case: Case) -> Output:
        return "not an Output"  # type: ignore[return-value]


def test_every_failure_kind_is_recorded_and_stays_in_the_denominator(tmp_path: Path) -> None:
    cases = [*DEFAULT_CASES, {"id": "q4", "input": "x", "expected": "y"}]
    usage = Usage(input_tokens=100, output_tokens=4000, reasoning_tokens=3500, cost_usd=0.25)
    target = FakeTarget(
        steps={
            "q1": Step(delay_s=0.5),  # timeout_s below is 0.05
            "q2": Step(error="truncated", value="Partial ans", echo_expected=False, usage=usage),
            "q3": Step(raise_exception=True),
        }
    )
    (result,) = run(tmp_path, [("a", target)], cases=cases, timeout_s=0.05)

    outputs = {o.case_id: o for o in load_run(result.directory).outputs}
    s = result.record.summary
    assert s.outputs_expected == 4 and s.outputs_recorded == 4
    assert s.target_errors == {"timeout": 1, "truncated": 1, "exception": 1}
    assert s.metrics["exact_match"].value == pytest.approx(0.25)
    assert outputs["q1"].error is not None and outputs["q1"].trace.latency_ms == pytest.approx(50)
    assert outputs["q2"].value == "Partial ans"  # the part already generated is kept
    assert outputs["q2"].trace.usage == usage  # and so is its usage: a truncated reply is billed
    assert outputs["q3"].trace.usage is None  # unknown, not zero
    assert result.record.cost.usage_unknown == 2
    assert result.record.cost.reasoning_tokens == 3500 + 0 * 2


def test_a_non_output_reply_is_invalid_output(tmp_path: Path) -> None:
    (result,) = run(tmp_path, [("a", _ReturnsNonsense())])

    assert result.record.summary.target_errors == {"invalid_output": 3}


def test_a_scorer_error_is_a_failed_score_not_a_crash(tmp_path: Path) -> None:
    bad_path = {"name": "exact_match", "field": "answer.text"}  # the output is a plain string
    (result,) = run(tmp_path, [("a", FakeTarget())], scorers=[bad_path, "exact_match"])

    s = result.record.summary
    stored = load_run(result.directory)
    assert s.scoring_errors == {"scorer_exception": 3}
    assert s.metrics['{"exact_match":{"field":"answer.text"}}'].value == 0.0
    assert s.metrics["exact_match"].value == 1.0
    assert stored.scores[0].error is not None
    assert stored.scores[0].error.message.startswith("KeyError")


def test_transport_errors_are_retried_with_capped_backoff(tmp_path: Path) -> None:
    sleeps = Sleeps()
    target = FakeTarget(steps={"q1": Step(transport_failures=2), "q2": Step(transport_failures=9)})
    (result,) = run(tmp_path, [("a", target)], RunOptions(sleep=sleeps), concurrency=1)

    outputs = {o.case_id: o for o in load_run(result.directory).outputs}
    assert outputs["q1"].error is None
    assert outputs["q2"].error is not None
    assert outputs["q2"].error.message.startswith("transport error after 3 retries")
    assert (target.calls["q1"], target.calls["q2"]) == (3, 4)
    assert sleeps.calls == [0.5, 1.0, 0.5, 1.0, 2.0]
    assert result.record.cost.calls == 3 + 4 + 1


def test_backoff_exponent_is_capped() -> None:
    assert [backoff_s(a, 0.5, 8.0) for a in range(6)] == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]
    assert backoff_s(10_000, 0.5, 8.0) == 8.0  # no overflow (design §3 L31)


# ---------------------------------------------------------------- budget


def test_max_calls_stops_the_run_as_partial(tmp_path: Path) -> None:
    (result,) = run(tmp_path, [("a", FakeTarget())], budget={"max_calls": 2}, concurrency=1)

    assert result.record.status == "partial"
    assert result.record.summary.outputs_recorded == 2
    assert result.record.summary.metrics["exact_match"].value == pytest.approx(2 / 3)
    assert [w.code for w in result.record.warnings] == ["budget_exhausted"]


FIVE_CASES = [{"id": f"q{i}", "input": i, "expected": i} for i in range(5)]


def test_max_cost_stops_once_spent_exceeds_it(tmp_path: Path) -> None:
    target = FakeTarget(default=Step(usage=Usage(cost_usd=0.25)))
    (result,) = run(
        tmp_path, [("a", target)], cases=FIVE_CASES, budget={"max_cost_usd": 0.5}, concurrency=1
    )

    # 0.25 and 0.5 are not over 0.5, so a third call is made; after it 0.75 is.
    assert sum(target.calls.values()) == 3
    assert result.record.status == "partial"


def test_a_zero_cost_budget_still_runs_free_calls(tmp_path: Path) -> None:
    free = FakeTarget(default=Step(usage=Usage(cost_usd=0.0)))
    (result,) = run(tmp_path, [("a", free)], budget={"max_cost_usd": 0}, concurrency=1)

    assert result.record.status == "complete"


def test_no_output_at_all_is_a_failed_run(tmp_path: Path) -> None:
    (result,) = run(tmp_path, [("a", FakeTarget())], budget={"max_calls": 0})

    assert result.record.status == "failed"
    assert result.record.summary.metrics["exact_match"].value == 0.0


# ---------------------------------------------------------------- repeats, concurrency, order


def test_a_non_deterministic_target_runs_twice_and_reports_the_spread(tmp_path: Path) -> None:
    target = FakeTarget(
        deterministic=False,
        per_repeat={("q1", 1): Step(value="Auckland", echo_expected=False)},
    )
    (result,) = run(tmp_path, [("a", target)], concurrency=1)

    m = result.record.summary.metrics["exact_match"]
    assert result.record.repeats == 2
    assert m.per_repeat == [1.0, pytest.approx(2 / 3)]
    assert m.min == pytest.approx(2 / 3)
    assert m.max == 1.0
    assert m.value == pytest.approx((0.5 + 1 + 1) / 3)  # per-case mean first, then over cases


def test_concurrency_is_bounded(tmp_path: Path) -> None:
    cases = [{"id": f"q{i}", "input": i, "expected": i} for i in range(6)]
    target = FakeTarget(default=Step(delay_s=0.02))
    run(tmp_path, [("a", target)], cases=cases, concurrency=2)

    assert target.max_in_flight == 2


def test_config_order_does_not_change_results(tmp_path: Path) -> None:
    def targets() -> list[tuple[str, Target]]:
        return [
            ("a", FakeTarget(config={"k": 1})),
            ("b", FakeTarget(config={"k": 2}, steps={"q2": Step(value="x", echo_expected=False)})),
        ]

    forward = run(tmp_path / "f", targets())
    backward = run(tmp_path / "b", list(reversed(targets())))

    assert [r.record.target.config["name"] for r in forward] == ["a", "b"]
    assert [scores_of(r) for r in forward] == [scores_of(r) for r in backward]


# ---------------------------------------------------------------- CI billing guard (design §18.13)


@pytest.mark.parametrize("billing", ["subscription", "per_token", "unknown"])
def test_ci_mode_refuses_targets_not_declared_free(tmp_path: Path, billing: str) -> None:
    target = FakeTarget(billing=billing)  # type: ignore[arg-type]

    with pytest.raises(ConfigError, match=f"a \\({billing}\\)"):
        run(tmp_path, [("a", target)], RunOptions(ci=True, sleep=Sleeps()))
    assert sum(target.calls.values()) == 0 and target.setups == 0


@pytest.mark.parametrize(("env", "suite_flag"), [(True, False), (False, True)])
def test_one_of_the_two_conditions_is_not_enough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: bool, suite_flag: bool
) -> None:
    if env:
        monkeypatch.setenv("LITE_EVAL_ALLOW_PAID", "1")
    with pytest.raises(ConfigError):
        run(
            tmp_path,
            [("a", FakeTarget(billing="per_token"))],
            RunOptions(ci=True, sleep=Sleeps()),
            allow_paid_in_ci=suite_flag,
        )


def test_both_conditions_allow_paid_and_the_run_records_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITE_EVAL_ALLOW_PAID", "1")
    (result,) = run(
        tmp_path,
        [("a", FakeTarget(billing="per_token"))],
        RunOptions(ci=True, sleep=Sleeps()),
        allow_paid_in_ci=True,
    )

    assert (result.record.ci.ci_mode, result.record.ci.paid_allowed) == (True, True)


def test_free_targets_run_in_ci_mode(tmp_path: Path) -> None:
    (result,) = run(tmp_path, [("a", FakeTarget())], RunOptions(ci=True, sleep=Sleeps()))

    assert (result.record.ci.ci_mode, result.record.ci.paid_allowed) == (True, False)


# ---------------------------------------------------------------- self-checks (design §18.17)


def _codes(result: RunResult) -> list[str]:
    return [w.code for w in result.record.warnings]


def test_identical_scores_across_different_configs_warn_both(tmp_path: Path) -> None:
    results = run(
        tmp_path, [("a", FakeTarget(config={"k": 1})), ("b", FakeTarget(config={"k": 2}))]
    )

    assert [_codes(r) for r in results] == [["identical_scores"], ["identical_scores"]]


def test_different_scores_do_not_warn(tmp_path: Path) -> None:
    results = run(
        tmp_path,
        [
            ("a", FakeTarget(config={"k": 1})),
            ("b", FakeTarget(config={"k": 2}, steps={"q1": Step(value="no", echo_expected=False)})),
        ],
    )

    assert [_codes(r) for r in results] == [[], []]


def test_feature_not_exercised_warns_until_a_decision_span_names_it(tmp_path: Path) -> None:
    (silent,) = run(tmp_path / "1", [("a", FakeTarget())], expect_features=["merge"])
    span = Span(kind="decision", attrs={"feature": "merge", "op": "NOOP"})
    (used,) = run(
        tmp_path / "2",
        [("a", FakeTarget(steps={"q2": Step(spans=[span])}))],
        expect_features=["merge"],
    )

    assert _codes(silent) == ["feature_not_exercised"]
    assert _codes(used) == []


def test_all_failed_synthetic_and_empty_group(tmp_path: Path) -> None:
    (result,) = run(
        tmp_path,
        [("a", FakeTarget(default=Step(value="nope", echo_expected=False)))],
        dataset_meta={"synthetic": True},
        expect_tags={"kind": ["fact", "multihop"]},
    )

    assert _codes(result) == ["synthetic_dataset", "empty_group", "all_failed"]
    assert "kind=multihop" in result.record.warnings[1].message


def test_empty_versions_warn_that_the_cache_cannot_see_engine_changes(tmp_path: Path) -> None:
    (result,) = run(tmp_path, [("a", FakeTarget(versions={}))])

    assert _codes(result) == ["unversioned_target"]


# ---------------------------------------------------------------- redaction (design §18.12)


def test_redaction_covers_outputs_spans_errors_scores_and_the_cache(tmp_path: Path) -> None:
    secret_url = "postgresql://app:hunter2@db/x"
    cases = [
        {"id": "q1", "input": "a", "expected": "mail ops@example.com"},
        {"id": "q2", "input": "b", "expected": "y"},
    ]
    span = Span(kind="llm", input={"api_key": "abc"}, output=secret_url, attrs={"dsn": secret_url})
    target = FakeTarget(
        steps={
            "q1": Step(value=f"use {secret_url}", echo_expected=False, spans=[span]),
            "q2": Step(error="invalid_output", value=None),
        }
    )
    (result,) = run(
        tmp_path,
        [("a", target)],
        cases=cases,
        scorers=["contains"],
        redact=["connection_strings", "api_keys", "emails"],
    )

    run_text = "".join(p.read_text() for p in result.directory.iterdir())
    cache_text = "".join(p.read_text() for p in (tmp_path / ".lite-eval").rglob("*.json"))
    for text in (run_text, cache_text):
        assert "hunter2" not in text and "ops@example.com" not in text and '"abc"' not in text
    stored = load_run(result.directory)
    assert stored.outputs[0].value == "use postgresql://app:«redacted:connection_strings»@db/x"
    assert stored.scores[0].evidence == {"missing": ["mail «redacted:emails»"]}
    assert result.record.redact.rules == ["connection_strings", "api_keys", "emails"]


def test_the_cache_entry_is_the_redacted_output(tmp_path: Path) -> None:
    target = FakeTarget(steps={"q1": Step(value="token=s3cr3t", echo_expected=False)})
    run(tmp_path, [("a", target)], redact=["api_keys"])

    entries = [json.loads(p.read_text()) for p in (tmp_path / ".lite-eval").rglob("*.json")]
    assert "token=«redacted:api_keys»" in [e["value"] for e in entries]


# ------------------------------------------------------- regressions found in the E1 review


def test_a_cache_hit_is_redacted_with_the_current_rules(tmp_path: Path) -> None:
    target = FakeTarget(steps={"q1": Step(value="token=s3cr3t", echo_expected=False)})
    run(tmp_path, [("a", target)])  # no redaction: the cache holds the plain value
    (result,) = run(tmp_path, [("a", FakeTarget())], redact=["api_keys"])

    assert result.record.cache.hits == 3
    assert "s3cr3t" not in (result.directory / "outputs.jsonl").read_text()


def test_the_budget_is_shared_by_every_config_of_the_suite(tmp_path: Path) -> None:
    a, b = FakeTarget(config={"k": 1}), FakeTarget(config={"k": 2})
    results = run(tmp_path, [("a", a), ("b", b)], budget={"max_calls": 4}, concurrency=1)

    assert sum(a.calls.values()) + sum(b.calls.values()) == 4
    assert [r.record.status for r in results] == ["complete", "partial"]


def test_a_target_sees_the_repeat_it_is_producing_even_with_a_warm_cache(tmp_path: Path) -> None:
    # Repeat 0 of q1 comes from the cache; the only call is repeat 1, which must get its own step.
    run(tmp_path, [("a", FakeTarget(deterministic=False))], repeats=1)
    target = FakeTarget(
        deterministic=False,
        per_repeat={("q1", 1): Step(value="Auckland", echo_expected=False)},
    )
    (result,) = run(tmp_path, [("a", target)], repeats=2)

    values = {(o.case_id, o.repeat): o.value for o in load_run(result.directory).outputs}
    assert values[("q1", 0)] == "Wellington"
    assert values[("q1", 1)] == "Auckland"


# ------------------------------------------------------- regressions found in the E1 Codex review


def test_a_transport_failure_on_the_last_budgeted_call_is_recorded(tmp_path: Path) -> None:
    sleeps = Sleeps()
    target = FakeTarget(steps={"q1": Step(transport_failures=9)})
    (result,) = run(
        tmp_path, [("a", target)], RunOptions(sleep=sleeps), budget={"max_calls": 1}, concurrency=1
    )

    outputs = load_run(result.directory).outputs
    assert [(o.case_id, o.error.kind if o.error else None) for o in outputs] == [
        ("q1", "exception")
    ]
    assert result.record.status == "partial"
    assert sleeps.calls == []  # no wait for a retry that the budget forbids


def test_max_calls_is_exact_under_concurrency(tmp_path: Path) -> None:
    target = FakeTarget(default=Step(delay_s=0.02))
    run(tmp_path, [("a", target)], budget={"max_calls": 1}, concurrency=4)

    assert sum(target.calls.values()) == 1
