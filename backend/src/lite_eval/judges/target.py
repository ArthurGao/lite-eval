"""A judge as the system under test, for calibration runs (design §9, §18.24).

A judging-pack case gives the judge what it would see (`input: {question, answer, sources}`) and
the label fixed by construction (`expected: {label}`); this target answers with the judge's
verdict, and `label_agreement` scores it. The target's config is the judge's identity (model,
route, temperature, thinking, rubric, prompt version), which a suite's judge scorer is checked
against when it names this run as its calibration.

In a suite: `python: lite_eval.judges.target:judge_target` with configs such as
`{name: qwen-nothink, rubric: grounded, provider: ollama, model: qwen3:8b, base_url: ...}`.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit, urlunsplit

from lite_eval import __version__
from lite_eval.contracts import Case, Output, Trace
from lite_eval.judges.client import JudgeCallFailed, JudgeClient, make_client
from lite_eval.judges.rubric import InvalidJudgeOutput, get_rubric, view_of_input
from lite_eval.judges.spec import JudgeSpec
from lite_eval.targets.base import TargetError

if TYPE_CHECKING:
    from lite_eval.datasets.local import Dataset

ClientFactory = Callable[[JudgeSpec, float | None], JudgeClient]


class JudgeTarget:
    name = "judge"
    deterministic = False  # a model, even at temperature 0: repeat it (methodology §4)

    def __init__(
        self,
        rubric: str,
        client_factory: ClientFactory | None = None,
        timeout_s: float | None = None,
        **judge: Any,
    ) -> None:
        self.rubric = get_rubric(rubric)
        self.spec = JudgeSpec.model_validate(judge)
        self._factory = client_factory or make_client
        self._timeout_s = timeout_s
        self._client: JudgeClient | None = None

    def identity(self) -> dict[str, Any]:
        return self.rubric.identity(self.spec)

    def config(self) -> dict[str, Any]:
        return self.identity()

    def versions(self) -> dict[str, str]:
        return {"lite-eval": __version__}

    def info(self) -> dict[str, str]:
        """Recorded in every run: the endpoint without userinfo, query or fragment, which can
        carry a key (design §18.12)."""
        url = urlsplit(self.spec.base_url)
        host = url.hostname or ""
        netloc = f"[{host}]" if ":" in host else host
        if url.port is not None:
            netloc += f":{url.port}"
        return {"base_url": urlunsplit((url.scheme, netloc, url.path, "", ""))}

    def billing(self) -> Literal["free", "subscription", "per_token", "unknown"]:
        return self.spec.billing

    def setup_identity(self) -> dict[str, Any]:
        return self.identity()

    def secrets(self) -> dict[str, str]:
        return dict(self._client.secrets) if self._client is not None else {}

    async def setup(self, dataset: "Dataset") -> None:
        del dataset
        self._client = self._factory(self.spec, self._timeout_s)

    async def teardown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def run(self, case: Case) -> Output:
        assert self._client is not None
        try:
            reply, verdict = await self.rubric.ask(self._client, view_of_input(case))
        except JudgeCallFailed as exc:
            raise TargetError("exception", str(exc)) from None
        except InvalidJudgeOutput as exc:
            assert exc.reply is not None
            raise TargetError(
                "invalid_output", str(exc), usage=exc.reply.usage, value=exc.reply.content[:500]
            ) from None
        value = {"label": verdict.verdict, "reason": verdict.reason, "evidence": verdict.evidence}
        return Output(case_id=case.id, value=value, trace=Trace(usage=reply.usage))


def judge_target(**config: Any) -> JudgeTarget:
    return JudgeTarget(**config)
