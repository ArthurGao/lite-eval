"""Paired comparison: statistics (design §18.2) and comparability (§18.3)."""

import json
from pathlib import Path
from typing import Any

import pytest

from lite_eval.analysis.compare import bootstrap_seed, mcnemar_p, paired_bootstrap
from lite_eval.api import compare_runs
from lite_eval.errors import ConfigError
from lite_eval.runner import RunResult
from lite_eval.targets.fake import FakeTarget, Step
from tests.helpers import execute_suite

CASES = [
    {"id": f"q{i:02d}", "input": i, "expected": f"a{i}", "tags": {"kind": "k"}} for i in range(20)
]


def wrong(*ids: str) -> FakeTarget:
    """Answers every case right except `ids`."""
    # The config names the behaviour: two different fakes must not share cache entries.
    return FakeTarget(
        steps={i: Step(value="no", echo_expected=False) for i in ids},
        config={"wrong": sorted(ids)},
    )


def run_one(root: Path, target: FakeTarget, name: str = "a", **spec: Any) -> RunResult:
    (result,) = execute_suite(root, [(name, target)], **{"cases": CASES, **spec})
    return result


# ---------------------------------------------------------------- statistics


def test_mcnemar_matches_the_hand_computed_exact_p() -> None:
    assert mcnemar_p(1, 9) == pytest.approx(22 / 1024, abs=1e-15)  # 2 * (1 + 9) / 2**10
    assert mcnemar_p(9, 1) == mcnemar_p(1, 9)
    assert mcnemar_p(0, 0) == 1.0
    assert mcnemar_p(5, 5) == 1.0  # 2 * P(X <= 5) > 1, capped


def test_mcnemar_agrees_with_scipy_binomtest() -> None:
    from scipy.stats import binomtest

    for b in range(12):
        for c in range(12):
            if b + c:
                assert mcnemar_p(b, c) == pytest.approx(
                    binomtest(min(b, c), b + c, 0.5).pvalue, abs=1e-12
                ), (b, c)


def test_bootstrap_is_reproducible_and_its_seed_follows_the_design() -> None:
    import hashlib

    xs, ys = [0.0, 1.0, 0.5, 0.2] * 5, [1.0, 1.0, 0.5, 0.4] * 5
    seed = bootstrap_seed("base", "cand", "mrr@10")

    assert seed == int(hashlib.sha256(b"base|cand|mrr@10").hexdigest()[:16], 16)
    assert paired_bootstrap(xs, ys, seed) == paired_bootstrap(xs, ys, seed)
    assert paired_bootstrap(xs, ys, seed)[0] > 0  # every case is at least as good: significant


def test_identical_values_give_a_zero_width_interval_that_is_not_significant() -> None:
    assert paired_bootstrap([0.3, 0.7, 0.1], [0.3, 0.7, 0.1], 1) == (0.0, 0.0)


# ---------------------------------------------------------------- comparisons of real runs


def test_a_single_regression_is_reported_first_and_not_significant(tmp_path: Path) -> None:
    base = run_one(tmp_path, wrong())
    cand = run_one(tmp_path, wrong("q07"), name="b")

    c = compare_runs(base.directory, cand.directory)

    d = next(m for m in c.metrics if m.metric == "exact_match")
    assert (d.test, d.n, d.discordant, d.significant) == ("mcnemar", 20, 1, False)
    assert d.delta == pytest.approx(-0.05)
    assert d.note.startswith("not significant (n=20, 1 discordant case; mcnemar p=1")
    assert c.changes[0].case_id == "q07" and c.changes[0].kind == "regressed"
    assert c.overlap == "full" and c.case_weight == pytest.approx(0.05)


def test_many_fixes_are_significant_and_listed_after_regressions(tmp_path: Path) -> None:
    broken = [f"q{i:02d}" for i in range(12)]
    base = run_one(tmp_path, wrong(*broken))
    cand = run_one(tmp_path, wrong("q19"), name="b")  # 12 fixed, 1 regressed

    c = compare_runs(base.directory, cand.directory)

    d = next(m for m in c.metrics if m.metric == "exact_match")
    assert d.p_value == pytest.approx(mcnemar_p(1, 12)) and d.significant is True
    assert next(x.kind for x in c.changes if x.metric == "exact_match") == "regressed"
    assert sum(x.kind == "fixed" for x in c.changes if x.metric == "exact_match") == 12


def test_repeats_switch_to_the_bootstrap(tmp_path: Path) -> None:
    base = run_one(tmp_path, wrong(), repeats=2)
    cand = run_one(tmp_path, wrong("q01"), name="b", repeats=2)

    d = next(
        m for m in compare_runs(base.directory, cand.directory).metrics if m.metric == "exact_match"
    )

    assert d.test == "bootstrap" and d.ci_low is not None and d.ci_high is not None
    assert d.ci_low <= 0.0 <= d.ci_high and d.significant is False


def test_different_dataset_names_are_refused(tmp_path: Path) -> None:
    base = run_one(tmp_path / "x", wrong())
    cand = run_one(tmp_path / "y", wrong(), dataset_meta={"name": "other"})

    with pytest.raises(ConfigError, match="different datasets"):
        compare_runs(base.directory, cand.directory)


def test_a_changed_dataset_compares_only_unchanged_cases(tmp_path: Path) -> None:
    base = run_one(tmp_path, wrong())
    cases = [dict(c) for c in CASES[:-1]] + [{"id": "q99", "input": 99, "expected": "a99"}]
    cases[0]["expected"] = "changed"
    cand = run_one(tmp_path, wrong(), name="b", cases=cases)

    c = compare_runs(base.directory, cand.directory)

    assert c.overlap == "partial_overlap"
    assert (c.changed_cases, c.added_cases, c.removed_cases) == (["q00"], ["q99"], ["q19"])
    assert next(m for m in c.metrics if m.metric == "exact_match").n == 18
    assert [w.code for w in c.warnings] == ["partial_overlap"]


def test_a_metric_whose_scorer_version_changed_is_incomparable(tmp_path: Path) -> None:
    base = run_one(tmp_path, wrong())
    cand = run_one(tmp_path, wrong(), name="b")
    run_json = cand.directory / "run.json"
    data = json.loads(run_json.read_text())
    data["scorers"][0]["version"] = "2"
    run_json.write_text(json.dumps(data))

    c = compare_runs(base.directory, cand.directory)

    assert {m.metric: m.status for m in c.metrics} == {"exact_match": "incomparable"}
    assert all(m.delta is None for m in c.metrics)


def test_every_worse_case_comes_before_any_better_one(tmp_path: Path) -> None:
    scorers = ["exact_match", {"name": "keywords", "expected": "kw"}]
    cases = [{"id": f"q{i}", "input": i, "expected": {"kw": [["a"], ["b"]]}} for i in range(4)]
    base_steps = {"q0": "a b", "q1": "a", "q2": "a b", "q3": "a"}
    cand_steps = {"q0": "a", "q1": "a b", "q2": "x", "q3": "a b"}

    def target(answers: dict[str, str]) -> FakeTarget:
        steps = {k: Step(value=v, echo_expected=False) for k, v in answers.items()}
        return FakeTarget(steps=steps, config=answers)

    base = run_one(tmp_path, target(base_steps), cases=cases, scorers=scorers)
    cand = run_one(tmp_path, target(cand_steps), name="b", cases=cases, scorers=scorers)

    changes = compare_runs(base.directory, cand.directory).changes
    worse = [c.candidate < c.baseline for c in changes]
    assert worse == sorted(worse, reverse=True)  # all True, then all False
    assert (changes[0].case_id, changes[0].metric) == ("q2", '{"keywords":{"expected":"kw"}}')


def test_a_difference_inside_a_runs_spread_over_repeats_is_flagged_within_noise(
    tmp_path: Path,
) -> None:
    # Baseline: q01 wrong on repeat 1 only -> per repeat [1.00, 0.95], spread 0.05.
    flaky = FakeTarget(
        per_repeat={("q01", 1): Step(value="no", echo_expected=False)},
        config={"flaky": True},
    )
    base = run_one(tmp_path, flaky, repeats=2)
    cand = run_one(tmp_path, wrong("q02"), name="b", repeats=2)  # mean 0.95: delta -0.025

    c = compare_runs(base.directory, cand.directory)

    d = next(m for m in c.metrics if m.metric == "exact_match")
    assert d.delta == pytest.approx(-0.025) and d.within_noise is True
    assert [w.code for w in c.warnings] == ["within_noise"]


def test_a_continuous_metric_that_happens_to_be_0_or_1_still_gets_the_bootstrap(
    tmp_path: Path,
) -> None:
    # mrr@10 is continuous; here every answer is at rank 1 or missing, so its values are 0 or 1.
    cases = [
        {"id": f"q{i}", "input": i, "expected": {"doc": "d", "evidence": "x"}} for i in range(6)
    ]
    found = Step(value=[{"doc": "d", "text": "x"}], echo_expected=False)
    missing = Step(value=[{"doc": "e", "text": "y"}], echo_expected=False)

    def target(misses: set[str]) -> FakeTarget:
        steps = {str(c["id"]): missing if c["id"] in misses else found for c in cases}
        return FakeTarget(steps=steps, config={"misses": sorted(misses)})

    spec: dict[str, Any] = {"cases": cases, "scorers": ["mrr@10"]}
    base = run_one(tmp_path, target(set()), **spec)
    cand = run_one(tmp_path, target({"q1"}), name="b", **spec)

    c = compare_runs(base.directory, cand.directory)

    assert c.metrics[0].metric == "mrr@10" and c.metrics[0].test == "bootstrap"
    assert [x.kind for x in c.changes] == ["changed"]  # not "regressed": mrr gives no verdict


def test_the_seeded_bootstrap_interval_is_pinned() -> None:
    # Recorded before the memory rework of paired_bootstrap: the interval must not move.
    xs = [(i * 7 % 11) / 10 for i in range(300)]
    ys = [min(1.0, (i * 5 % 13) / 10) for i in range(300)]

    low, high = paired_bootstrap(xs, ys, 12345)

    assert low == pytest.approx(0.023333333333333317, abs=1e-12)
    assert high == pytest.approx(0.13033333333333336, abs=1e-12)
