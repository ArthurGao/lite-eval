"""Setup sharing and the warm-up call (design §18.18)."""

from pathlib import Path
from typing import Any

import pytest

from lite_eval.contracts import Case, Output, Usage
from lite_eval.errors import RunError
from lite_eval.runner import RunResult
from lite_eval.store import load_run
from lite_eval.targets.base import Target, TargetError, warming_up
from lite_eval.targets.fake import FakeTarget, Step
from tests.helpers import execute_suite


def run(root: Path, targets: list[tuple[str, Target]], **spec: Any) -> list[RunResult]:
    return execute_suite(root, targets, **spec)


def fake(cid: str, log: list[str], index: str = "bge-m3", **kw: object) -> FakeTarget:
    t = FakeTarget(config={"id": cid}, setup_identity={"index": index}, **kw)  # type: ignore[arg-type]
    t.log = log
    return t


def test_configs_with_one_setup_identity_share_one_setup(tmp_path: Path) -> None:
    log: list[str] = []
    a, b, c = fake("a", log), fake("b", log), fake("c", log)
    results = run(tmp_path, [("c", c), ("a", a), ("b", b)])

    assert (a.setups, b.setups, c.setups) == (1, 0, 0)
    assert b.shared_from is a and c.shared_from is a
    # The leader sets up, followers share; teardown after the whole group, followers first.
    assert log == ["setup:a", "share:b", "share:c", "teardown:c", "teardown:b", "teardown:a"]
    assert all("setup_not_shared" not in [w.code for w in r.record.warnings] for r in results)


def test_a_follower_that_cannot_share_sets_up_again_with_a_warning(tmp_path: Path) -> None:
    log: list[str] = []
    a, b = fake("a", log), fake("b", log, can_share_setup=False)
    results = run(tmp_path, [("a", a), ("b", b)])

    assert (a.setups, b.setups) == (1, 1)
    codes = [[w.code for w in r.record.warnings] for r in results]
    assert ("setup_not_shared" in codes[0], "setup_not_shared" in codes[1]) == (False, True)
    assert log == ["setup:a", "setup:b", "teardown:b", "teardown:a"]


def test_different_setup_identities_are_separate_groups(tmp_path: Path) -> None:
    log: list[str] = []
    a, b = fake("a", log, index="bge-m3"), fake("b", log, index="minilm")
    run(tmp_path, [("a", a), ("b", b)])

    assert log == ["setup:a", "teardown:a", "setup:b", "teardown:b"]


def test_the_warm_up_is_charged_but_never_recorded(tmp_path: Path) -> None:
    usage = Usage(input_tokens=7, cost_usd=0.5)
    target = FakeTarget(default=Step(usage=usage))
    (result,) = run(tmp_path, [("a", target)])

    outputs = load_run(result.directory).outputs
    assert target.warmups == 1
    assert len(outputs) == 3  # no fourth output from the warm-up
    assert result.record.cost.calls == 4
    assert result.record.cost.input_tokens == 4 * 7
    assert result.record.cost.cost_usd == 4 * 0.5


def test_no_warm_up_when_every_output_comes_from_the_cache(tmp_path: Path) -> None:
    run(tmp_path, [("a", FakeTarget())])
    again = FakeTarget()
    (result,) = run(tmp_path, [("a", again)])

    assert (again.warmups, result.record.cost.calls) == (0, 0)


class _TeardownFails(FakeTarget):
    async def teardown(self) -> None:
        await super().teardown()
        raise RuntimeError("teardown broke")


class _SetupFails(FakeTarget):
    async def _share_setup_from(self, leader: FakeTarget) -> None:
        raise RuntimeError("share broke")


def test_every_target_is_torn_down_and_the_first_error_is_kept(tmp_path: Path) -> None:
    log: list[str] = []
    a = fake("a", log)
    b = _TeardownFails(config={"id": "b"}, setup_identity={"index": "bge-m3"})
    c = _SetupFails(config={"id": "c"}, setup_identity={"index": "bge-m3"})
    b.log = c.log = log

    with pytest.raises(RunError, match="setup of config 'c' failed: share broke") as info:
        run(tmp_path, [("a", a), ("b", b), ("c", c)])

    assert log[-2:] == ["teardown:b", "teardown:a"]  # b's failure does not skip the leader
    assert "teardown of config 'b' failed: teardown broke" in "\n".join(info.value.__notes__)


def test_a_teardown_failure_after_a_clean_run_is_a_run_error(tmp_path: Path) -> None:
    b = _TeardownFails(config={"id": "b"})

    with pytest.raises(RunError, match="teardown of config 'b' failed: teardown broke"):
        run(tmp_path, [("b", b)])


class _WarmUpErrs(FakeTarget):
    async def run(self, case: Case) -> Output:
        if warming_up.get():
            raise TargetError("truncated", "cut", usage=Usage(output_tokens=9, cost_usd=0.75))
        return await super().run(case)


def test_a_warm_up_that_fails_after_spending_is_still_charged(tmp_path: Path) -> None:
    (result,) = run(tmp_path, [("a", _WarmUpErrs(default=Step(usage=Usage())))])

    assert result.record.cost.cost_usd == 0.75
    assert result.record.cost.output_tokens == 9
