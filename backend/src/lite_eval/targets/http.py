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
from lite_eval.redact import KnownValues, env_secrets
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


class HttpTarget(SpecIdentity):
    name = "http"

    def __init__(self, spec: HttpSpec, params: dict[str, JsonValue]) -> None:
        self.spec: HttpSpec = spec
        self.params = params
        used: dict[str, str] = {}
        self._headers = {
            k: resolve_env(v, f"http header {k!r}", used) for k, v in spec.headers.items()
        }
        self._known = KnownValues(env_secrets(used))
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

    def secrets(self) -> dict[str, str]:
        """The resolved `${ENV}` header values: the runner masks them by value in everything it
        keeps, however a server echoes them (design §18.12)."""
        return dict(self._known.values)

    async def run(self, case: Case) -> Output:
        """Latency is the runner's wall clock (it measures every call, failed ones too)."""
        try:
            status, body = await self._fetch(case)
        except httpx2.TimeoutException as exc:  # a TransportError subclass: caught first
            raise TargetError("timeout", f"http timeout: {type(exc).__name__}") from exc
        except httpx2.TransportError as exc:
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc
        # Masked here, whole, before anything cuts it: the error page below is cut to 500, and
        # pydantic's message shows a value's first and last 24 characters; the runner masks again,
        # but a cut secret is unrecognisable. JSON is UTF-8 (RFC 8259).
        ok = 200 <= status < 300
        try:  # an error page is shown as it comes; a reply must be UTF-8 to be read
            text = self._known.text(body.decode("utf-8", errors="strict" if ok else "replace"))
        except UnicodeDecodeError as exc:
            raise TargetError("invalid_output", f"reply is not UTF-8: {exc}") from None
        if not ok:
            raise TargetError("http_status", f"HTTP {status}: {text[:500]}")
        try:
            reply = json.loads(text)
            value = self._output.select(reply)
            trace = Trace.model_validate(self._trace.select(reply)) if self._trace else Trace()
        except (ValueError, LookupError, ValidationError) as exc:
            raise TargetError("invalid_output", f"{type(exc).__name__}: {exc}") from exc
        return Output(case_id=case.id, value=value, trace=trace)
