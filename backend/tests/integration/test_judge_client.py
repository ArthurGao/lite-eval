"""Judge clients against a local fake server (design §9, §18.24): the request each channel sends,
how a structured reply and its usage are read, and how failures are classified."""

import asyncio
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from lite_eval.judges.client import JudgeCallFailed, make_client
from lite_eval.judges.spec import JudgeSpec
from lite_eval.targets.base import TransportError

SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["yes", "no"]}},
    "required": ["verdict"],
}
MESSAGES = [{"role": "system", "content": "judge"}, {"role": "user", "content": "is it?"}]
KEY = "-".join(["sk", "judge", "0123456789abcdefghij"])  # assembled: no key-shaped literal


class State:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.mode = "ok"


def _handler(state: State) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _reply(self, status: int, body: Any) -> None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append({"path": self.path, "headers": dict(self.headers), **body})
            if state.mode == "500":
                self._reply(
                    500, f"upstream says no, key {self.headers.get('Authorization')}".encode()
                )
            elif state.mode == "drop":
                self.close_connection = True
            elif self.path == "/api/chat":
                self._reply(
                    200,
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"verdict": "yes"}',
                            "thinking": "short thought",
                        },
                        "done": True,
                        "done_reason": "stop",
                        "prompt_eval_count": 40,
                        "eval_count": 7,
                    },
                )
            elif self.path == "/v1/chat/completions":
                self._reply(
                    200,
                    {
                        "choices": [
                            {"message": {"role": "assistant", "content": '{"verdict": "no"}'}}
                        ],
                        "usage": {
                            "prompt_tokens": 1000,
                            "completion_tokens": 300,
                            "completion_tokens_details": {"reasoning_tokens": 200},
                        },
                    },
                )
            else:
                self._reply(404, {"error": "no"})

    return Handler


@pytest.fixture
def server() -> Iterator[tuple[str, State]]:
    state = State()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", state
    finally:
        httpd.shutdown()
        httpd.server_close()


def call(spec: JudgeSpec) -> Any:
    async def go() -> Any:
        client = make_client(spec)
        try:
            return await client.complete(MESSAGES, SCHEMA)
        finally:
            await client.aclose()

    return asyncio.run(go())


def test_ollama_sends_the_schema_think_and_temperature_and_reads_usage(
    server: tuple[str, State],
) -> None:
    url, state = server
    spec = JudgeSpec(provider="ollama", model="qwen3:8b", base_url=url, think=False, temperature=0)
    reply = call(spec)
    (sent,) = state.requests
    assert sent["path"] == "/api/chat"
    assert sent["model"] == "qwen3:8b" and sent["stream"] is False and sent["think"] is False
    assert sent["format"] == SCHEMA and sent["options"] == {"temperature": 0}
    assert sent["messages"] == MESSAGES
    assert reply.content == '{"verdict": "yes"}' and reply.thinking == "short thought"
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (40, 7)
    assert reply.usage.cost_usd == 0.0


def test_openai_sends_a_strict_json_schema_and_prices_the_usage(
    server: tuple[str, State], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, state = server
    monkeypatch.setenv("JUDGE_KEY", KEY)
    spec = JudgeSpec(
        provider="openai",
        model="judge-model",
        base_url=f"{url}/v1",
        channel="llm-gateway:api",
        think="low",
        api_key_env="JUDGE_KEY",
        billing="per_token",
        price={"input_per_mtok": 3.0, "output_per_mtok": 15.0},
    )
    reply = call(spec)
    (sent,) = state.requests
    assert sent["path"] == "/v1/chat/completions"
    assert sent["headers"]["Authorization"] == f"Bearer {KEY}"
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "verdict", "strict": True, "schema": SCHEMA},
    }
    assert sent["reasoning_effort"] == "low" and sent["temperature"] == 0
    assert reply.content == '{"verdict": "no"}'
    usage = reply.usage
    assert (usage.input_tokens, usage.output_tokens, usage.reasoning_tokens) == (1000, 300, 200)
    assert usage.cost_usd == pytest.approx(1000 * 3.0 / 1e6 + 300 * 15.0 / 1e6)


def test_a_failed_call_names_the_status_and_never_the_key(
    server: tuple[str, State], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, state = server
    state.mode = "500"
    monkeypatch.setenv("JUDGE_KEY", KEY)
    spec = JudgeSpec(provider="openai", model="m", base_url=f"{url}/v1", api_key_env="JUDGE_KEY")
    with pytest.raises(JudgeCallFailed) as failed:
        call(spec)
    assert "HTTP 500" in str(failed.value) and KEY not in str(failed.value)
    assert "«redacted:env:JUDGE_KEY»" in str(failed.value)


def test_a_dropped_connection_is_a_transport_error_the_runner_retries(
    server: tuple[str, State],
) -> None:
    url, state = server
    state.mode = "drop"
    with pytest.raises(TransportError):
        call(JudgeSpec(provider="ollama", model="m", base_url=url))


def test_a_missing_key_variable_is_a_config_error_naming_it() -> None:
    from lite_eval.errors import ConfigError

    spec = JudgeSpec(
        provider="openai", model="m", base_url="http://x/v1", api_key_env="NO_SUCH_KEY"
    )
    with pytest.raises(ConfigError, match="NO_SUCH_KEY"):
        make_client(spec)


def test_the_identity_is_what_decides_a_verdict_and_nothing_else() -> None:
    """Model, route, temperature and think are the instrument (L3); host and key are not."""
    a = JudgeSpec(provider="ollama", model="qwen3:8b", base_url="http://a:1", think=False)
    b = JudgeSpec(provider="ollama", model="qwen3:8b", base_url="http://b:2", think=False)
    assert (
        a.identity()
        == b.identity()
        == {
            "provider": "ollama",
            "channel": "ollama",
            "model": "qwen3:8b",
            "temperature": 0.0,
            "think": False,
        }
    )
    assert a.identity() != a.model_copy(update={"think": True}).identity()
    assert a.identity() != a.model_copy(update={"channel": "other"}).identity()
