"""Model-judge scorers in the runner (design §9, §18.24), with FakeJudge: verdicts, unusable
replies, failed and slow calls, retries, the budget, concurrency, cost, rescore, and what a
suite may not do with a judge."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from lite_eval.contracts import Usage
from lite_eval.errors import ConfigError
from lite_eval.judges.client import JudgeCallFailed, JudgeClient, JudgeReply, Messages
from lite_eval.judges.fake import FakeJudge, by_marker
from lite_eval.judges.rubric import get_rubric
from lite_eval.judges.spec import JudgeSpec
from lite_eval.runner import RunOptions, RunResult, rescore_run
from lite_eval.scorers.compose import build_scorer
from lite_eval.store import load_run
from lite_eval.targets.base import TransportError
from lite_eval.targets.fake import FakeTarget, Step
from tests.helpers import Sleeps, execute_suite

JUDGE = {
    "provider": "ollama",
    "model": "qwen3:8b",
    "base_url": "http://127.0.0.1:1",  # never reached: FakeJudge
    "think": False,
    "billing": "free",
}
GROUNDED = {"judge": {"rubric": "grounded"}}
BAD = "a claim that is UNSUPPORTED"


def answer(text: str) -> dict[str, Any]:
    return {"answer": text, "sources": [{"text": "Wellington is the capital of NZ."}]}


CASES = [
    {"id": "q1", "input": {"question": "capital of NZ?"}, "expected": answer("Wellington [1]")},
    {"id": "q2", "input": {"question": "capital of NZ?"}, "expected": answer(f"Auckland. {BAD}")},
    {"id": "q3", "input": {"question": "capital of NZ?"}, "expected": answer("Wellington.")},
]


class Judges:
    """A judge_factory that hands out one FakeJudge per role, kept for assertions."""

    def __init__(self, make: Any = None) -> None:
        self.make = make or (
            lambda spec: FakeJudge(spec, by_marker(BAD, "unsupported", "grounded"))
        )
        self.made: list[JudgeClient] = []

    def __call__(self, spec: JudgeSpec, timeout_s: float) -> JudgeClient:
        client = self.make(spec)
        self.made.append(client)
        return client


def run(
    root: Path,
    factory: Judges,
    target: FakeTarget | None = None,
    sleeps: Sleeps | None = None,
    **spec: Any,
) -> RunResult:
    spec.setdefault("scorers", [GROUNDED])
    spec.setdefault("judges", {"grounded": JUDGE})
    options = RunOptions(judge_factory=factory, sleep=sleeps or Sleeps())
    (result,) = execute_suite(root, [("a", target or FakeTarget())], options, cases=CASES, **spec)
    return result


def scores(result: RunResult) -> dict[str, Any]:
    return {s.case_id: s for s in load_run(result.directory).scores}


def test_a_judge_scores_each_output_and_records_the_instrument(tmp_path: Path) -> None:
    judges = Judges()
    result = run(tmp_path, judges)
    by_case = scores(result)
    assert [by_case[c].value for c in ("q1", "q2", "q3")] == [1.0, 0.0, 1.0]
    assert by_case["q2"].reason.startswith("unsupported")
    assert by_case["q2"].evidence == {"verdict": "unsupported", "evidence": [BAD]}
    record = by_case["q1"].judge
    assert record is not None
    assert (record.role, record.model, record.channel) == ("grounded", "qwen3:8b", "ollama")
    assert record.prompt_version == get_rubric("grounded").prompt_version
    assert (record.temperature, record.think) == (0.0, False)
    assert record.usage == Usage(input_tokens=100, output_tokens=10)
    assert record.raw == {"content": '{"verdict": "grounded", "reason": "fake", "evidence": []}'}
    cost = result.record.cost
    assert (cost.judge_calls, cost.judge_input_tokens, cost.judge_output_tokens) == (3, 300, 30)
    assert cost.input_tokens == 40  # the target's own: 3 outputs + the warm-up call, apart
    (client,) = judges.made
    assert isinstance(client, FakeJudge) and client.closed and len(client.calls) == 3
    # the judge saw the question, the numbered sources and the answer
    prompt = client.calls[0][1]["content"]
    assert "capital of NZ?" in prompt and "[1] Wellington is the capital of NZ." in prompt


def test_an_unusable_reply_is_a_scoring_error_with_the_reply_kept(tmp_path: Path) -> None:
    judges = Judges(lambda spec: FakeJudge(spec, lambda m: "not json at all"))
    result = run(tmp_path, judges)
    score = scores(result)["q1"]
    assert score.value == 0.0 and score.passed is False
    assert score.error is not None and score.error.kind == "invalid_judge_output"
    assert score.judge is not None and score.judge.raw == {"content": "not json at all"}
    assert sum(result.record.summary.scoring_errors.values()) == 3
    other = Judges(lambda spec: FakeJudge(spec, lambda m: {"verdict": "maybe", "reason": "",
                                                            "evidence": []}))  # fmt: skip
    wrong = scores(run(tmp_path / "b", other))["q1"]
    assert wrong.error is not None and "'maybe' is not one of" in wrong.error.message


def test_a_failed_call_and_a_slow_one_are_judge_call_failed_in_the_denominator(
    tmp_path: Path,
) -> None:
    failing = Judges(lambda spec: FakeJudge(spec, lambda m: JudgeCallFailed("HTTP 500: down")))
    score = scores(run(tmp_path / "a", failing))["q1"]
    assert score.error is not None and score.error.kind == "judge_call_failed"
    assert "HTTP 500" in score.error.message and score.value == 0.0

    class Slow(FakeJudge):
        async def complete(self, messages: Messages, schema: dict[str, Any]) -> JudgeReply:
            await asyncio.sleep(5)
            return await super().complete(messages, schema)

    slow = Judges(lambda spec: Slow(spec, by_marker(BAD, "unsupported", "grounded")))
    result = run(tmp_path / "b", slow, judge_timeout_s=0.05)
    score = scores(result)["q1"]
    assert score.error is not None and score.error.message == "no reply within 0.05s"


def test_a_transport_error_is_retried_with_backoff(tmp_path: Path) -> None:
    attempts: dict[str, int] = {}

    def flaky(messages: Messages) -> Any:
        key = messages[1]["content"]
        attempts[key] = attempts.get(key, 0) + 1
        if attempts[key] <= 2:
            return TransportError("connection reset")
        return {"verdict": "grounded", "reason": "", "evidence": []}

    sleeps = Sleeps()
    result = run(tmp_path, Judges(lambda spec: FakeJudge(spec, flaky)), sleeps=sleeps)
    assert all(s.value == 1.0 for s in scores(result).values())
    assert result.record.cost.judge_calls == 9  # 3 outputs x (2 failures + 1 success)
    assert len(sleeps.calls) == 6


def test_the_budget_counts_the_judges_and_a_judge_it_stops_makes_the_run_partial(
    tmp_path: Path,
) -> None:
    priced = {**JUDGE, "price": {"input_per_mtok": 1000.0, "output_per_mtok": 0.0}}
    judges = Judges(
        lambda spec: FakeJudge(spec, by_marker(BAD, "unsupported", "grounded"),
                               usage=Usage(input_tokens=1000, output_tokens=0, cost_usd=1.0))
    )  # fmt: skip
    result = run(
        tmp_path, judges, budget={"max_cost_usd": 1.5}, concurrency=1, judges={"grounded": priced}
    )
    errors = [s.error for s in scores(result).values() if s.error is not None]
    assert [e.message for e in errors] == ["budget reached"]
    assert result.record.status == "partial"
    assert result.record.cost.judge_cost_usd == pytest.approx(2.0)


def test_an_unusable_reply_still_costs_and_counts_against_the_budget(tmp_path: Path) -> None:
    """The judge was paid for a reply it got wrong: its usage counts, so the budget stops."""
    priced = {**JUDGE, "price": {"input_per_mtok": 1000.0, "output_per_mtok": 0.0}}
    judges = Judges(
        lambda spec: FakeJudge(spec, lambda m: "not json at all",
                               usage=Usage(input_tokens=1000, output_tokens=0, cost_usd=1.0))
    )  # fmt: skip
    result = run(
        tmp_path, judges, budget={"max_cost_usd": 1.5}, concurrency=1, judges={"grounded": priced}
    )
    kinds = [s.error.kind for s in scores(result).values() if s.error is not None]
    assert sorted(kinds) == ["invalid_judge_output", "invalid_judge_output", "judge_call_failed"]
    assert result.record.status == "partial"


def test_judges_run_concurrently_up_to_the_suite_s_concurrency(tmp_path: Path) -> None:
    state = {"now": 0, "peak": 0}

    class Counting(FakeJudge):
        async def complete(self, messages: Messages, schema: dict[str, Any]) -> JudgeReply:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            await asyncio.sleep(0.02)
            state["now"] -= 1
            return await super().complete(messages, schema)

    judges = Judges(lambda spec: Counting(spec, by_marker(BAD, "unsupported", "grounded")))
    run(tmp_path, judges, concurrency=2)
    assert state["peak"] == 2


def test_a_target_error_is_not_judged(tmp_path: Path) -> None:
    judges = Judges()
    target = FakeTarget(steps={"q2": Step(error="timeout")})
    score = scores(run(tmp_path, judges, target))["q2"]
    assert score.reason == "target error: timeout" and score.judge is None
    (client,) = judges.made
    assert isinstance(client, FakeJudge) and len(client.calls) == 2


def test_rescore_judges_the_stored_outputs_again_without_calling_the_target(
    tmp_path: Path,
) -> None:
    target = FakeTarget()
    first = run(tmp_path, Judges(), target)
    before = dict(target.calls)
    no = {"verdict": "unsupported", "reason": "", "evidence": []}
    again = Judges(lambda spec: FakeJudge(spec, lambda m: no))
    result = rescore_run(first.directory, options=RunOptions(judge_factory=again))
    assert [s.value for s in scores(result).values()] == [0.0, 0.0, 0.0]
    (client,) = again.made
    assert isinstance(client, FakeJudge) and len(client.calls) == 3
    assert result.record.cost.judge_calls == 3
    assert dict(target.calls) == before  # the target was not called again


def test_a_judge_s_identity_is_its_version() -> None:
    judges = {"grounded": JudgeSpec.model_validate(JUDGE)}
    base = build_scorer(GROUNDED, judges)
    for change in ({"model": "qwen3:14b"}, {"think": True}, {"temperature": 0.2},
                   {"channel": "llm-gateway:api"}):  # fmt: skip
        other = {"grounded": JudgeSpec.model_validate({**JUDGE, **change})}
        assert build_scorer(GROUNDED, other).version != base.version, change
    with pytest.MonkeyPatch.context() as mp:  # a prompt edit is a new instrument too
        mp.setattr(type(get_rubric("grounded")), "prompt_version", property(lambda r: "edited"))
        assert build_scorer(GROUNDED, judges).version != base.version
    host = {"grounded": JudgeSpec.model_validate({**JUDGE, "base_url": "http://other:2"})}
    assert build_scorer(GROUNDED, host).version == base.version
    assert base.name == '{"judge":{"rubric":"grounded"}}'


def test_a_suite_cannot_use_a_judge_it_does_not_map_or_put_one_in_a_composite() -> None:
    judges = {"grounded": JudgeSpec.model_validate(JUDGE)}
    with pytest.raises(ConfigError, match=r"no `judges.other`"):
        build_scorer({"judge": {"rubric": "grounded", "role": "other"}}, judges)
    with pytest.raises(ConfigError, match="cannot be part of a composite"):
        build_scorer({"all_of": ["exact_match", GROUNDED]}, judges)
    with pytest.raises(ConfigError, match="unknown judge rubric"):
        build_scorer({"judge": {"rubric": "nope"}}, judges)


def test_a_judge_s_key_is_masked_in_what_the_run_keeps(tmp_path: Path) -> None:
    key = "-".join(["sk", "judge", "key", "0123456789abcdef"])  # assembled: no key-shaped literal

    judges = Judges(
        lambda spec: FakeJudge(spec, lambda m: {"verdict": "grounded", "reason": f"used {key}",
                                                "evidence": []},
                               secrets={"env:JUDGE_KEY": key})
    )  # fmt: skip
    result = run(tmp_path, judges)
    text = "".join(f.read_text() for f in result.directory.iterdir())
    assert key not in text and "«redacted:env:JUDGE_KEY»" in text
    assert result.record.redact.known == ["env:JUDGE_KEY"]


def test_ci_refuses_a_judge_not_declared_free_unless_both_allow_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design §18.13 covers judges too: a per-token judge is a paid call like a target."""
    paid = {**JUDGE, "provider": "openai", "billing": "per_token"}
    judges = Judges()
    with pytest.raises(ConfigError, match=r"CI mode refuses .*judge grounded \(per_token\)"):
        execute_suite(
            tmp_path / "a",
            [("a", FakeTarget())],
            RunOptions(judge_factory=judges, ci=True),
            cases=CASES,
            scorers=[GROUNDED],
            judges={"grounded": paid},
        )
    assert judges.made == []  # refused before any judge was made
    monkeypatch.setenv("LITE_EVAL_ALLOW_PAID", "1")
    (result,) = execute_suite(
        tmp_path / "b",
        [("a", FakeTarget())],
        RunOptions(judge_factory=judges, ci=True),
        cases=CASES,
        scorers=[GROUNDED],
        judges={"grounded": paid},
        allow_paid_in_ci=True,
    )
    assert result.record.cost.judge_calls == 3


def test_judge_calls_count_against_the_suite_s_max_calls(tmp_path: Path) -> None:
    """A judge call is a model call: the suite's call budget covers it (four target calls with
    the warm-up, then one judge call, then nothing)."""
    judges = Judges()
    result = run(tmp_path, judges, budget={"max_calls": 5}, concurrency=1)
    assert result.record.cost.calls == 4
    kinds = sorted(str(s.error.kind if s.error else "ok") for s in scores(result).values())
    assert kinds == ["judge_call_failed", "judge_call_failed", "ok"]
    assert result.record.cost.judge_calls == 1 and result.record.status == "partial"


def test_rescore_in_ci_refuses_a_judge_not_declared_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-judging is a paid call like the first judging: the CI guard covers rescore too, whether
    CI mode comes from the options or from `CI=true` (the CLI passes no options)."""
    paid = {**JUDGE, "billing": "per_token"}
    first = run(tmp_path, Judges(), judges={"grounded": paid})
    judges = Judges()
    with pytest.raises(ConfigError, match=r"CI mode refuses .*judge grounded \(per_token\)"):
        rescore_run(first.directory, options=RunOptions(judge_factory=judges, ci=True))
    assert judges.made == []
    monkeypatch.setenv("CI", "true")
    with pytest.raises(ConfigError, match=r"CI mode refuses .*judge grounded"):
        rescore_run(first.directory)  # would otherwise reach the (unreachable) judge endpoint


def test_a_paid_judge_allowed_in_ci_is_recorded_as_paid_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design §18.13: the run records that the allowance was used. A free target judged by a
    per-token judge made paid calls, so its record says so, like a paid target's."""
    monkeypatch.setenv("LITE_EVAL_ALLOW_PAID", "1")
    paid = {**JUDGE, "provider": "openai", "billing": "per_token"}
    (result,) = execute_suite(
        tmp_path,
        [("a", FakeTarget())],
        RunOptions(judge_factory=Judges(), ci=True),
        cases=CASES,
        scorers=[GROUNDED],
        judges={"grounded": paid},
        allow_paid_in_ci=True,
    )
    assert (result.record.ci.ci_mode, result.record.ci.paid_allowed) == (True, True)
