"""Opt-in (`uv run pytest -m ollama`): the grounded judge on the real local qwen3:8b, thinking off
(design §9). Two answers with obvious verdicts: the reply must pass the schema, carry its usage,
and get both right."""

import asyncio

import pytest

from lite_eval.contracts import Case, Output
from lite_eval.judges.client import make_client
from lite_eval.judges.spec import JudgeSpec
from lite_eval.scorers.compose import build_scorer
from lite_eval.scorers.judge import JudgeScorer

pytestmark = pytest.mark.ollama

SPEC = JudgeSpec(
    provider="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434", think=False
)
SOURCE = "Pour-over coffee: use 1 gram of coffee to 16 grams of water, at 92 to 96 degrees."


def judge(answer: str) -> tuple[str, int]:
    scorer = build_scorer({"judge": {"rubric": "grounded"}}, {"grounded": SPEC})
    assert isinstance(scorer, JudgeScorer)
    case = Case(id="c", input={"question": "What ratio for pour-over?"})
    output = Output(case_id="c", value={"answer": answer, "sources": [{"text": SOURCE}]})

    async def go() -> tuple[str, int]:
        client = make_client(SPEC, 120.0)
        try:
            score = await scorer.ascore(case, output, client)
        finally:
            await client.aclose()
        assert score.judge is not None and score.judge.usage is not None
        assert isinstance(score.evidence, dict)
        return str(score.evidence["verdict"]), score.judge.usage.output_tokens

    return asyncio.run(go())


def test_qwen3_judges_an_obvious_pair_of_answers() -> None:
    verdict, tokens = judge("Use 1 gram of coffee to 16 grams of water [1].")
    assert verdict == "grounded" and tokens > 0
    verdict, _ = judge("Use 1 gram of coffee to 30 grams of water, and add salt [1].")
    assert verdict == "unsupported"
