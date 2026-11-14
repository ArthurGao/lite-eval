"""HTTP targets: one request per case (design §18.11, §18.23).

A suite is trusted local configuration: it can reach any URL. Header values may name environment
variables as `${NAME}`; they are resolved when the target is built and never recorded (the record
keeps the template). The body is a Jinja2 template rendered in a sandbox with `case` (the case as
JSON) and `config` (the config's parameters).
"""

import json
import os
import re
from typing import TYPE_CHECKING, Any, Literal

import httpx2
from jinja2 import StrictUndefined, TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import Field, JsonValue, ValidationError, field_validator

from lite_eval.contracts import Case, Output, Trace
from lite_eval.errors import ConfigError
from lite_eval.targets.base import MB, SpecBase, SpecIdentity, TargetError, TransportError
from lite_eval.targets.jsonpath import JsonPath, compile_path

if TYPE_CHECKING:
    from lite_eval.datasets.local import Dataset

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class HttpSpec(SpecBase):
    """`targets: [{http: {...}, configs: [...]}]` in a suite."""

    url: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    output: str = "$"
    trace: str | None = None  # a JSONPath to a Trace object ({usage, latency_ms, spans})
    timeout_s: float | None = Field(default=None, gt=0)  # None: the suite's timeout_s
    max_response_bytes: int = Field(default=10 * MB, gt=0)

    @field_validator("output", "trace")
    @classmethod
    def _paths(cls, path: str | None) -> str | None:
        if path is not None:
            compile_path(path)
        return path


def resolve_env(template: str, what: str, used: dict[str, str]) -> str:
    """`${NAME}` replaced from the environment; each value used is added to `used`."""

    def value(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in os.environ:
            raise ConfigError(f"{what} needs the environment variable {name}, which is not set")
        used[name] = os.environ[name]
        return os.environ[name]

    return _ENV.sub(value, template)


class _Masker:
    """Replaces the resolved header secrets wherever a reply or an error repeats them: a server
    that echoes the request (a 401 page, a debug endpoint) must not put one into a record."""

    def __init__(self, secrets: dict[str, str]) -> None:
        # Longest first, so a secret containing another is masked whole; empty values mask nothing.
        self._pairs = sorted(
            ((v, f"«redacted:env:{k}»") for k, v in secrets.items() if v),
            key=lambda p: -len(p[0]),
        )

    def text(self, value: str) -> str:
        for secret, marker in self._pairs:
            value = value.replace(secret, marker)
        return value

    def json(self, value: Any) -> Any:
        if not self._pairs:
            return value
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.json(v) for v in value]
        if isinstance(value, dict):
            return {self.text(k): self.json(v) for k, v in value.items()}
        return value


class HttpTarget(SpecIdentity):
    name = "http"

    def __init__(self, spec: HttpSpec, params: dict[str, JsonValue]) -> None:
        self.spec: HttpSpec = spec
        self.params = params
        secrets: dict[str, str] = {}
        self._headers = {
            k: resolve_env(v, f"http header {k!r}", secrets) for k, v in spec.headers.items()
        }
        self._mask = _Masker(secrets)
        if spec.body is not None and not any(k.lower() == "content-type" for k in spec.headers):
            self._headers["Content-Type"] = "application/json"
        env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
        try:
            self._body = env.from_string(spec.body) if spec.body is not None else None
        except TemplateSyntaxError as exc:
            raise ConfigError(f"http body template: {exc}") from exc
        self._output: JsonPath = compile_path(spec.output)
        self._trace: JsonPath | None = compile_path(spec.trace) if spec.trace else None
        self._client: httpx2.AsyncClient | None = None

    def describe(self) -> str:
        return f"{self.spec.method} {self.spec.url}"

    def config(self) -> dict[str, Any]:
        s = self.spec
        request = {
            "method": s.method,
            "url": s.url,
            "headers": dict(s.headers),  # the templates, never the resolved values
            "body": s.body,
            "output": s.output,
            "trace": s.trace,
        }
        return {**self.params, "http": request}

    async def setup(self, dataset: "Dataset") -> None:
        del dataset
        timeout = httpx2.Timeout(self.spec.timeout_s)  # None: the runner's timeout alone
        self._client = httpx2.AsyncClient(timeout=timeout, follow_redirects=False)

    async def teardown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _render(self, case: Case) -> str | None:
        if self._body is None:
            return None
        return self._body.render(case=case.model_dump(mode="json"), config=self.params)

    async def _fetch(self, case: Case) -> tuple[int, bytearray]:
        if self._client is None:
            raise RuntimeError("http target used before setup")
        limit = self.spec.max_response_bytes
        async with self._client.stream(
            self.spec.method, self.spec.url, content=self._render(case), headers=self._headers
        ) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise TargetError(
                        "truncated", f"reply larger than max_response_bytes ({limit})"
                    )
            return response.status_code, body

    async def run(self, case: Case) -> Output:
        """Every message and value leaves masked; the original exception is not chained, so
        nothing carries an echoed secret further (design §18.11)."""
        try:
            output = await self._run(case)
        except TargetError as exc:
            masked = self._mask.text(exc.message)
            raise TargetError(
                exc.kind, masked, usage=exc.usage, latency_ms=exc.latency_ms
            ) from None
        except TransportError as exc:
            raise TransportError(self._mask.text(str(exc))) from None
        return output

    async def _run(self, case: Case) -> Output:
        """Latency is the runner's wall clock (it measures every call, failed ones too)."""
        try:
            status, body = await self._fetch(case)
        except httpx2.TimeoutException as exc:  # a TransportError subclass: caught first
            raise TargetError("timeout", f"http timeout: {type(exc).__name__}") from exc
        except httpx2.TransportError as exc:
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc
        if not 200 <= status < 300:
            text = body[:500].decode("utf-8", errors="replace")
            raise TargetError("http_status", f"HTTP {status}: {text}")
        try:
            reply = self._mask.json(json.loads(body))
            value = self._output.select(reply)
            trace = Trace.model_validate(self._trace.select(reply)) if self._trace else Trace()
        except (ValueError, LookupError, ValidationError) as exc:
            raise TargetError("invalid_output", f"{type(exc).__name__}: {exc}") from exc
        return Output(case_id=case.id, value=value, trace=trace)
