"""Model-judge scorers (design §9, §18.24): `{judge: {rubric: grounded, role: grounded}}`.

The role is looked up in the suite's `judges:` (code names roles, never models). The scorer's
version is a hash of the judge's identity and the rubric's prompt version, so a changed model,
route, temperature, thinking or prompt is another scorer and comparisons say so (§18.3).

A judge is called asynchronously (`ascore`); the runner gives it its client, its concurrency,
timeout, retries and budget. A judge scorer cannot sit inside a composite (they score
synchronously). `calibration: {run, min_agreement}` names the judging run that calibrated this
judge; the runner checks it before any call (§18.24).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from lite_eval.canonical import canonical_json, sha256_hex
from lite_eval.contracts import Case, JudgeRecord, Output, Score, Verdict
from lite_eval.errors import ConfigError
from lite_eval.judges.calibration import Calibration
from lite_eval.judges.client import JudgeClient, JudgeReply
from lite_eval.judges.rubric import InvalidJudgeOutput, Rubric, get_rubric, view_of
from lite_eval.judges.spec import JudgeSpec
from lite_eval.scorers.base import Spec, make_score, spec_name

_PARAMS = {"rubric", "role", "calibration"}


class JudgedInvalid(InvalidJudgeOutput):
    """An unusable reply, with the record of the call (the raw reply stays visible)."""

    def __init__(self, message: str, record: JudgeRecord) -> None:
        super().__init__(message)
        self.record = record


@dataclass(frozen=True)
class JudgeScorer:
    rubric: Rubric
    role: str
    judge: JudgeSpec
    params: dict[str, Any] = field(default_factory=dict)
    calibration: Calibration | None = None  # parsed from params; checked by the runner

    @property
    def spec(self) -> Spec:
        return {"judge": self.params}

    @cached_property
    def name(self) -> str:
        return spec_name(self.spec)

    @cached_property
    def identity(self) -> dict[str, Any]:
        return self.rubric.identity(self.judge)

    @cached_property
    def version(self) -> str:
        return sha256_hex(canonical_json(self.identity))[:12]

    @property
    def verdict(self) -> Verdict:
        return "value"

    def score(self, case: Case, output: Output) -> Score:
        raise TypeError(f"{self.name} is a model judge: the runner scores it with ascore()")

    def _record(self, reply: JudgeReply) -> JudgeRecord:
        raw: dict[str, Any] = {"content": reply.content}
        if reply.thinking:
            raw["thinking"] = reply.thinking
        return JudgeRecord(
            role=self.role,
            model=self.judge.model,
            channel=self.judge.route,
            prompt_version=self.rubric.prompt_version,
            temperature=self.judge.temperature,
            think=self.judge.think,
            raw=raw,
            usage=reply.usage,
        )

    async def ascore(self, case: Case, output: Output, client: JudgeClient) -> Score:
        try:
            reply, verdict = await self.rubric.ask(client, view_of(case, output))
        except InvalidJudgeOutput as exc:
            assert exc.reply is not None
            raise JudgedInvalid(str(exc), self._record(exc.reply)) from None
        record = self._record(reply)
        ok = verdict.verdict == self.rubric.passing
        return make_score(
            self,
            output,
            value=float(ok),
            passed=ok,
            reason=f"{verdict.verdict}: {verdict.reason}",
            evidence={"verdict": verdict.verdict, "evidence": verdict.evidence},
            judge=record,
        )


def judge_scorer(
    kind: str, params: dict[str, Any], judges: Mapping[str, JudgeSpec] | None
) -> JudgeScorer | None:
    if kind != "judge":
        return None
    unknown = set(params) - _PARAMS
    if unknown:
        raise ConfigError(f"judge scorer: unknown params {sorted(unknown)}")
    if "rubric" not in params:
        raise ConfigError("judge scorer needs `rubric`")
    rubric = get_rubric(str(params["rubric"]))
    role = str(params.get("role", rubric.name))
    if not judges or role not in judges:
        raise ConfigError(
            f"judge scorer: the suite has no `judges.{role}` (a role maps to a model there)"
        )
    calibration = Calibration.parse(params["calibration"]) if "calibration" in params else None
    return JudgeScorer(rubric, role, judges[role], dict(params), calibration)
