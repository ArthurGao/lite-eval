"""A judge for tests (design §9: tests never call a real model): `decide(messages)` returns the
reply, as a dict (sent as JSON), a raw string (to test unparsable replies) or an exception to
raise (a failed call, a transport error)."""

import json
from collections.abc import Callable
from typing import Any

from lite_eval.contracts import Usage
from lite_eval.judges.client import JudgeReply, Messages
from lite_eval.judges.spec import JudgeSpec

Decide = Callable[[Messages], dict[str, Any] | str | Exception]


class FakeJudge:
    def __init__(
        self,
        spec: JudgeSpec,
        decide: Decide,
        usage: Usage | None = None,
        secrets: dict[str, str] | None = None,  # as a real client's api key would be
    ) -> None:
        self.spec = spec
        self.decide = decide
        self.usage = usage or Usage(input_tokens=100, output_tokens=10)
        self.calls: list[Messages] = []
        self.closed = False
        self.secrets = dict(secrets or {})

    async def complete(self, messages: Messages, schema: dict[str, Any]) -> JudgeReply:
        self.calls.append(messages)
        reply = self.decide(messages)
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return JudgeReply(content, self.usage)

    async def aclose(self) -> None:
        self.closed = True


def by_marker(marker: str, flagged: str, otherwise: str) -> Decide:
    """A judge that answers `flagged` when the prompt contains `marker`: enough to tell a
    constructed corruption from its base in tests."""

    def decide(messages: Messages) -> dict[str, Any]:
        text = "\n".join(m["content"] for m in messages)
        verdict = flagged if marker in text else otherwise
        return {
            "verdict": verdict,
            "reason": "fake",
            "evidence": [marker] if marker in text else [],
        }

    return decide
