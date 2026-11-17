"""Calling a judge model (design §9, §18.24): the only code in lite-eval that does.

Two protocols over httpx2 (no SDK): Ollama's `/api/chat` with `format` (a JSON schema) and `think`,
and OpenAI-compatible chat completions (llm-gateway) with a strict `json_schema` response format
(both checked against the docs: Ollama 0.40.15 docs/api.md, OpenAI API reference). A connection
failure is a TransportError (the runner retries it); any other failure is JudgeCallFailed, which
the runner records as a `judge_call_failed` scoring error. The key is read from the environment
when the client is made and masked by value in every message.
"""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx2

from lite_eval.contracts import Usage
from lite_eval.judges.spec import JudgeSpec
from lite_eval.redact import KnownValues
from lite_eval.targets.base import TransportError
from lite_eval.targets.http import resolve_env

Messages = list[dict[str, str]]


class JudgeCallFailed(Exception):
    """A call that will not succeed by retrying: an error status, a timeout, an empty reply."""


@dataclass(frozen=True)
class JudgeReply:
    content: str  # the structured reply, as JSON text
    usage: Usage
    thinking: str | None = None


class JudgeClient(Protocol):
    spec: JudgeSpec
    secrets: dict[str, str]  # values to mask by value (design §18.12), e.g. `env:NAME` -> key

    async def complete(self, messages: Messages, schema: dict[str, Any]) -> JudgeReply: ...

    async def aclose(self) -> None: ...


def _cost(spec: JudgeSpec, input_tokens: int, output_tokens: int) -> float:
    if spec.price is None:
        return 0.0
    return (
        input_tokens * spec.price.input_per_mtok + output_tokens * spec.price.output_per_mtok
    ) / 1e6


class _HttpJudge:
    path: str

    def __init__(self, spec: JudgeSpec, timeout_s: float | None) -> None:
        self.spec = spec
        headers = {}
        used: dict[str, str] = {}
        if spec.api_key_env:
            bearer = "Bearer ${" + spec.api_key_env + "}"
            headers["Authorization"] = resolve_env(bearer, f"judge {spec.model}", used)
        self.secrets = {f"env:{name}": value for name, value in used.items()}
        self._known = KnownValues(self.secrets)
        self._http = httpx2.AsyncClient(
            base_url=spec.base_url.rstrip("/"),
            headers=headers,
            timeout=httpx2.Timeout(timeout_s),
            follow_redirects=False,
        )

    def body(self, messages: Messages, schema: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def read(self, data: dict[str, Any]) -> JudgeReply:
        raise NotImplementedError

    async def complete(self, messages: Messages, schema: dict[str, Any]) -> JudgeReply:
        try:
            response = await self._http.post(self.path, json=self.body(messages, schema))
        except httpx2.TimeoutException as exc:  # a TransportError subclass: caught first
            raise JudgeCallFailed(f"judge timeout: {type(exc).__name__}") from None
        except httpx2.TransportError as exc:
            raise TransportError(self._known.text(f"{type(exc).__name__}: {exc}")) from None
        if not 200 <= response.status_code < 300:
            raise JudgeCallFailed(f"HTTP {response.status_code}: {self._cut(response.text)}")
        try:
            return self.read(response.json())
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise JudgeCallFailed(
                f"unreadable reply ({type(exc).__name__}): {self._cut(response.text)}"
            ) from None

    def _cut(self, text: str) -> str:
        """Masked whole, then cut (design §18.12): cutting first could leave half a key."""
        return self._known.text(text)[:300]

    async def aclose(self) -> None:
        await self._http.aclose()


class OllamaJudge(_HttpJudge):
    path = "/api/chat"

    def body(self, messages: Messages, schema: dict[str, Any]) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": self.spec.temperature}
        if self.spec.max_tokens is not None:
            options["num_predict"] = self.spec.max_tokens
        return {
            "model": self.spec.model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "think": self.spec.think,
            "options": options,
        }

    def read(self, data: dict[str, Any]) -> JudgeReply:
        message = data["message"]
        tokens_in, tokens_out = (
            int(data.get("prompt_eval_count", 0)),
            int(data.get("eval_count", 0)),
        )
        usage = Usage(
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cost_usd=_cost(self.spec, tokens_in, tokens_out),
        )
        return JudgeReply(str(message["content"]), usage, message.get("thinking") or None)


class OpenAIJudge(_HttpJudge):
    path = "/chat/completions"

    def body(self, messages: Messages, schema: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.spec.model,
            "messages": messages,
            "temperature": self.spec.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "verdict", "strict": True, "schema": schema},
            },
        }
        if isinstance(self.spec.think, str):
            body["reasoning_effort"] = self.spec.think
        if self.spec.max_tokens is not None:
            body["max_tokens"] = self.spec.max_tokens
        return body

    def read(self, data: dict[str, Any]) -> JudgeReply:
        u = data.get("usage") or {}
        tokens_in, tokens_out = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        reasoning = int((u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0))
        usage = Usage(
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            reasoning_tokens=reasoning,
            cost_usd=_cost(self.spec, tokens_in, tokens_out),
        )
        return JudgeReply(str(data["choices"][0]["message"]["content"]), usage)


def make_client(spec: JudgeSpec, timeout_s: float | None = None) -> JudgeClient:
    if spec.provider == "ollama":
        return OllamaJudge(spec, timeout_s)
    return OpenAIJudge(spec, timeout_s)
