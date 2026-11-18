"""The two-phase runner against FakeTarget (design §6, §16 E1 acceptance)."""

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from lite_eval.contracts import Case, Output, Span, Trace, Usage
from lite_eval.datasets.local import load_dataset
from lite_eval.errors import ConfigError, RunError
from lite_eval.report.terminal import render_run
from lite_eval.runner import RunOptions, RunResult, backoff_s, execute
from lite_eval.store import load_run
from lite_eval.suite import load_suite
from lite_eval.targets.base import Target
from lite_eval.targets.fake import FakeTarget, Step
from tests.helpers import DEFAULT_CASES, Sleeps, write_suite


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
    assert "exact_match:pass_rate" not in rec.summary.metrics  # binary: it would repeat the value
    assert rec.summary.outputs_expected == 3
    assert rec.summary.case_weight == pytest.approx(1 / 3)
    assert rec.summary.groups["kind"] == {
        "fact": {"exact_match": 1.0},
        "math": {"exact_match": 1.0},
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
    assert result.record.cost.calls == 1 + 3 + 4 + 1  # the warm-up call, then q1, q2, q3
    assert target.warmups == 1


def test_a_retry_the_budget_stops_says_so_not_that_retries_ran_out(tmp_path: Path) -> None:
    """Four calls in the budget: the warm-up and three tries of q1. Its error names the budget,
    not the retry limit, although one try short of it (the message is the only trace)."""
    target = FakeTarget(steps={"q1": Step(transport_failures=9)})
    (result,) = run(tmp_path, [("a", target)], budget={"max_calls": 4}, concurrency=1)

    outputs = {o.case_id: o for o in load_run(result.directory).outputs}
    assert target.calls["q1"] == 3
    assert outputs["q1"].error is not None
    assert outputs["q1"].error.message.startswith("transport error and the budget allows no retry")
    assert result.record.status == "partial"


def test_backoff_exponent_is_capped() -> None:
    assert [backoff_s(a, 0.5, 8.0) for a in range(6)] == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]
    assert backoff_s(10_000, 0.5, 8.0) == 8.0  # no overflow (design §3 L31)


# ---------------------------------------------------------------- budget


def test_a_run_mostly_of_target_errors_says_so_up_front(tmp_path: Path) -> None:
    """Failures count (rule 4), so a run whose calls failed (a quota ran out) scores low as if
    the target were bad. The record warns how many outputs are errors and what they were, and
    the report's header shows the count, so the number is not read as a measurement."""
    target = FakeTarget(steps={"q1": Step(raise_exception=True), "q2": Step(raise_exception=True)})
    (result,) = run(tmp_path, [("a", target)])

    (warning,) = [w for w in result.record.warnings if w.code == "target_errors"]
    assert warning.message.startswith("2 of 3 outputs are target errors (exception: 2;")
    assert "RuntimeError: boom on q1" in warning.message
    assert "2 errors" in render_run(result.record, str(result.directory), result.scores)


def test_a_run_with_few_target_errors_gets_no_such_warning(tmp_path: Path) -> None:
    cases = [{"id": f"c{i:02d}", "input": {"q": i}, "expected": "x"} for i in range(11)]
    target = FakeTarget(steps={"c00": Step(raise_exception=True)})
    (result,) = run(tmp_path, [("a", target)], cases=cases)

    assert result.record.summary.errored_outputs == 1  # 1 of 11: under a tenth
    assert "target_errors" not in {w.code for w in result.record.warnings}


def test_max_calls_stops_the_run_as_partial(tmp_path: Path) -> None:
    # 3 calls: the warm-up (design §18.18 charges it to the budget), then two cases.
    (result,) = run(tmp_path, [("a", FakeTarget())], budget={"max_calls": 3}, concurrency=1)

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

    # The warm-up spends 0.25; 0.25 and 0.5 are not over 0.5, so two cases run; then 0.75 is.
    assert (target.warmups, sum(target.calls.values())) == (1, 2)
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
    # 6 calls: a's warm-up and 3 cases, then b's warm-up and 1 case.
    results = run(tmp_path, [("a", a), ("b", b)], budget={"max_calls": 6}, concurrency=1)

    assert sum(a.calls.values()) + sum(b.calls.values()) == 4
    assert (a.warmups, b.warmups) == (1, 1)
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
        tmp_path, [("a", target)], RunOptions(sleep=sleeps), budget={"max_calls": 2}, concurrency=1
    )  # the warm-up, then q1's single allowed call

    outputs = load_run(result.directory).outputs
    assert [(o.case_id, o.error.kind if o.error else None) for o in outputs] == [
        ("q1", "exception")
    ]
    assert result.record.status == "partial"
    assert sleeps.calls == []  # no wait for a retry that the budget forbids


def test_max_calls_is_exact_under_concurrency(tmp_path: Path) -> None:
    target = FakeTarget(default=Step(delay_s=0.02))
    run(tmp_path, [("a", target)], budget={"max_calls": 2}, concurrency=4)  # warm-up + 1

    assert (target.warmups, sum(target.calls.values())) == (1, 1)


def test_a_scorer_with_a_partial_value_also_reports_its_pass_rate(tmp_path: Path) -> None:
    cases = [{"id": "k1", "input": "x", "expected": [["shanghai"], ["acme"]]}]
    target = FakeTarget(steps={"k1": Step(value="lives in shanghai", echo_expected=False)})
    (result,) = run(tmp_path, [("a", target)], cases=cases, scorers=["keywords"])

    m = result.record.summary.metrics
    assert (m["keywords"].value, m["keywords:pass_rate"].value) == (0.5, 0.0)


def test_a_composite_of_binary_scorers_is_binary_too(tmp_path: Path) -> None:
    both = {"all_of": ["exact_match", "contains"]}
    (result,) = run(tmp_path, [("a", FakeTarget())], scorers=[both])

    assert list(result.record.summary.metrics) == ['{"all_of":["exact_match","contains"]}']


SECRET = "s3cr3t-value-0123456789"


class _Leaky(FakeTarget):
    """Repeats its own secret everywhere a target can put text (E4d, design §18.12)."""

    def secrets(self) -> dict[str, str]:
        return {"env:SERVICE_KEY": SECRET, "env:PIN": "1234"}

    async def run(self, case: Case) -> Output:
        if case.id == "q3":
            from lite_eval.targets.base import TargetError

            raise TargetError("exception", f"login failed for key {SECRET}")
        span = Span(kind="tool", name="call", attrs={"header": f"Bearer {SECRET}"})
        return Output(
            case_id=case.id,
            value=f"answer from {SECRET}",
            trace=Trace(spans=[span]),
        )

    def artifacts(self) -> dict[str, str]:
        return {"target.log": f"started with {SECRET}\n"}

    def abandoned(self) -> str | None:
        return f"gave up (key {SECRET})"


def test_a_target_s_declared_secrets_are_masked_in_everything_the_run_keeps(
    tmp_path: Path,
) -> None:
    (result,) = run(tmp_path, [("a", _Leaky())], cache="on")
    root_text = "".join(p.read_text(errors="replace") for p in tmp_path.rglob("*") if p.is_file())
    assert SECRET not in root_text  # outputs, spans, errors, scores, artifact, warnings, cache
    outputs = {o.case_id: o for o in load_run(result.directory).outputs}
    assert outputs["q1"].value == "answer from «redacted:env:SERVICE_KEY»"
    assert outputs["q1"].trace.spans[0].attrs == {"header": "Bearer «redacted:env:SERVICE_KEY»"}
    error = outputs["q3"].error
    assert error is not None and error.message == "login failed for key «redacted:env:SERVICE_KEY»"
    log = (result.directory / "target.log").read_text()
    assert log == "started with «redacted:env:SERVICE_KEY»\n"
    codes = {w.code: w.message for w in result.record.warnings}
    assert "«redacted:env:SERVICE_KEY»" in codes["target_abandoned"]
    assert "env:PIN" in codes["short_secret"] and "1234" not in codes["short_secret"]
    assert result.record.redact.known == ["env:PIN", "env:SERVICE_KEY"]  # names only


def test_a_declared_secret_in_a_score_s_evidence_is_masked_too(tmp_path: Path) -> None:
    """A scorer may repeat the expected value (`contains` lists what is missing): scoring goes
    through the same per-config redaction as generation (E4d)."""
    cases = [{"id": "q1", "input": "x", "expected": [f"key {SECRET}"]}]
    (result,) = run(tmp_path, [("a", _Leaky())], cases=cases, scorers=["contains"])
    scores = (result.directory / "scores.jsonl").read_text()
    assert SECRET not in scores
    assert "key «redacted:env:SERVICE_KEY»" in scores


class _LeakySetup(_Leaky):
    async def setup(self, dataset: Any) -> None:
        raise RuntimeError(f"cannot connect with {SECRET}")


class _LeakyTeardown(_Leaky):
    async def teardown(self) -> None:
        raise RuntimeError(f"cannot close session {SECRET}")


def test_setup_and_teardown_failures_mask_the_target_s_secrets(tmp_path: Path) -> None:
    """The CLI prints these, and pytest prints a traceback of the cause: neither keeps a
    declared secret (E4d /simplify)."""
    with pytest.raises(RunError) as setup:
        run(tmp_path / "a", [("a", _LeakySetup())])
    assert SECRET not in str(setup.value) and setup.value.__cause__ is None
    assert "cannot connect with «redacted:env:SERVICE_KEY»" in str(setup.value)
    with pytest.raises(RunError) as teardown:
        run(tmp_path / "b", [("a", _LeakyTeardown())])
    assert SECRET not in str(teardown.value) and teardown.value.__cause__ is None
    assert "cannot close session «redacted:env:SERVICE_KEY»" in str(teardown.value)


class _SecretInConfig(_Leaky):
    """A target that reads its credential into its config, and says so in describe()."""

    def config(self) -> dict[str, Any]:
        return {"api_key": SECRET, "mode": "x"}

    def describe(self) -> str:
        return f"calls the service with {SECRET}"


def test_a_declared_secret_in_the_target_config_or_description_is_masked(tmp_path: Path) -> None:
    """Codex E4d: TargetRecord.config and the announce line; the cache key keeps the value."""
    from lite_eval.runner import prepare_targets

    (result,) = run(tmp_path, [("a", _SecretInConfig())])
    assert result.record.target.config["api_key"] == "«redacted:env:SERVICE_KEY»"
    assert SECRET not in (result.directory / "run.json").read_text()
    lines: list[str] = []
    module = tmp_path / "secret_target.py"
    module.write_text(
        "from tests.integration.test_runner import _SecretInConfig\n"
        "def make(**_): return _SecretInConfig()\n"
    )
    suite = load_suite(
        write_suite(
            tmp_path / "b", targets=[{"python": "secret_target:make", "configs": [{"name": "a"}]}]
        )
    )
    import sys

    sys.path.insert(0, str(tmp_path))
    try:
        prepare_targets(suite, announce=lines.append)
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("secret_target", None)
    assert lines == ["config a: calls the service with «redacted:env:SERVICE_KEY»"]


def test_scores_are_written_output_by_output_with_every_scorer_in_suite_order(
    tmp_path: Path,
) -> None:
    """scores.jsonl is in a fixed order (each output, then the suite's scorers), so the same run
    writes the same bytes and a reader can rely on it."""
    (result,) = run(tmp_path, [("a", FakeTarget())], scorers=["exact_match", "contains"])

    written = [(s.case_id, s.scorer) for s in load_run(result.directory).scores]
    cases = sorted({case_id for case_id, _ in written})
    assert written == [(c, s) for c in cases for s in ("exact_match", "contains")]
