"""What a judge judges (design §9, §18.24): a rubric names its verdicts and which one passes,
renders its prompt from `prompts/judge_<name>_{system,user}.j2`, and gives the JSON schema the
reply must follow. Its prompt version is a hash of the template text, never a hand-kept number,
so an edited prompt is another scorer (L3)."""

from dataclasses import dataclass
from functools import cached_property
from importlib.resources import files
from typing import Any

from jinja2 import Environment, StrictUndefined, Template
from pydantic import BaseModel, Field, ValidationError

from lite_eval.canonical import sha256_hex
from lite_eval.contracts import Case, Output, ScoreError
from lite_eval.errors import ConfigError
from lite_eval.judges.client import JudgeClient, JudgeReply
from lite_eval.judges.spec import JudgeSpec

_PROMPTS = files("lite_eval") / "prompts"
_ENV = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


class Verdict(BaseModel):
    """A judge's reply (design §9, §18.24): the schema the judge is held to has no `score`."""

    verdict: str
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)


class InvalidJudgeOutput(Exception):
    """The reply is not a verdict this rubric allows: a scoring error, never a pass. `reply` is
    the call that produced it (its usage was paid for, its text stays visible)."""

    def __init__(self, message: str, reply: JudgeReply | None = None) -> None:
        super().__init__(message)
        self.reply = reply

    def error(self) -> ScoreError:
        return ScoreError(kind="invalid_judge_output", message=str(self))


@dataclass(frozen=True)
class Rubric:
    name: str
    verdicts: tuple[str, ...]
    passing: str

    @cached_property
    def _texts(self) -> tuple[str, str]:
        """The system and user templates, read once: what is hashed is what is sent."""

        def read(part: str) -> str:
            return (_PROMPTS / f"judge_{self.name}_{part}.j2").read_text(encoding="utf-8")

        return read("system"), read("user")

    @cached_property
    def prompt_version(self) -> str:
        return sha256_hex(self._texts[0] + "\0" + self._texts[1])[:12]

    @cached_property
    def _system(self) -> str:
        return _ENV.from_string(self._texts[0]).render()

    @cached_property
    def _user(self) -> Template:
        return _ENV.from_string(self._texts[1])

    @cached_property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": list(self.verdicts)},
                "reason": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["verdict", "reason", "evidence"],
            "additionalProperties": False,
        }

    def identity(self, judge: JudgeSpec) -> dict[str, Any]:
        """The instrument (L3): the judge's identity, the rubric and its prompt version. A judge
        scorer's version and a calibration run's target config are both this dict, so a
        calibration is checked key for key against the judge that would use it."""
        return {**judge.identity(), "rubric": self.name, "prompt_version": self.prompt_version}

    def messages(self, view: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self._system},
            {"role": "user", "content": self._user.render(**view)},
        ]

    def parse(self, content: str) -> Verdict:
        try:
            verdict = Verdict.model_validate_json(content)
        except ValidationError as exc:
            raise InvalidJudgeOutput(f"not a verdict: {exc.errors()[0]['msg']}") from None
        if verdict.verdict not in self.verdicts:
            raise InvalidJudgeOutput(f"verdict {verdict.verdict!r} is not one of {self.verdicts}")
        return verdict

    async def ask(self, client: JudgeClient, view: dict[str, Any]) -> tuple[JudgeReply, Verdict]:
        """One judge call: the scorer and the calibration target both judge through here, so a
        judge sees the same prompt in calibration as in use. An unusable reply raises
        InvalidJudgeOutput carrying the reply."""
        reply = await client.complete(self.messages(view), self.schema)
        try:
            return reply, self.parse(reply.content)
        except InvalidJudgeOutput as exc:
            raise InvalidJudgeOutput(str(exc), reply) from None


def _view(question: Any, value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        answer, sources = value.get("answer", ""), value.get("sources")
    else:
        answer, sources = value, None
    texts = [
        str(s.get("text", "")) if isinstance(s, dict) else str(s)
        for s in (sources if isinstance(sources, list) else [])
    ]
    return {"question": str(question), "answer": str(answer), "sources": texts}


def view_of(case: Case, output: Output) -> dict[str, Any]:
    """What a judge sees of a scored output: the case's question, the output's answer and sources
    (`{answer, sources: [{text} | str]}`, or a bare answer string)."""
    question = case.input.get("question", "") if isinstance(case.input, dict) else case.input
    return _view(question, output.value)


def view_of_input(case: Case) -> dict[str, Any]:
    """What a judge sees in a calibration case: the input holds question, answer and sources."""
    data = case.input if isinstance(case.input, dict) else {}
    return _view(data.get("question", ""), data)


RUBRICS: dict[str, Rubric] = {
    "grounded": Rubric(name="grounded", verdicts=("grounded", "unsupported"), passing="grounded"),
}


def get_rubric(name: str) -> Rubric:
    if name not in RUBRICS:
        raise ConfigError(f"unknown judge rubric {name!r}; available: {sorted(RUBRICS)}")
    return RUBRICS[name]
