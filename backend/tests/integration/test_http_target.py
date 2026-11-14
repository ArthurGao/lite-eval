"""HTTP targets against a real local server (design §18.11, §18.23): every failure kind."""

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from lite_eval.errors import ConfigError
from lite_eval.runner import prepare_targets
from lite_eval.store import load_run
from lite_eval.suite import load_suite
from lite_eval.targets.jsonpath import compile_path
from tests.helpers import Sleeps, output_errors, run_prepared, write_suite

SECRET = "tok-9f8e7d6c5b4a"


class Server:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.drops = 0  # /flaky: close this many connections without a reply first


def _handler(state: Server) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _reply(self, status: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            request = json.loads(raw)
            state.requests.append({"path": self.path, "headers": dict(self.headers), **request})
            case = request["case"]
            answer = {"data": {"answer": case["expected"], "hits": [{"id": "a"}, {"id": "b"}]}}
            answer["meta"] = {"usage": {"input_tokens": 3, "output_tokens": 2}}
            if self.path == "/flaky" and state.drops:
                state.drops -= 1
                self.close_connection = True  # no reply: a transport error for the client
                return
            if self.path == "/status":
                self._reply(503, b"upstream down")
            elif self.path == "/text":
                self._reply(200, b"not json", "text/plain")
            elif self.path == "/slow":
                time.sleep(1.0)
                self._reply(200, json.dumps(answer).encode())
            elif self.path == "/big":
                self._reply(200, json.dumps({"data": {"answer": "x" * 5000}}).encode())
            else:
                self._reply(200, json.dumps(answer).encode())

    return Handler


@pytest.fixture
def server() -> Iterator[tuple[str, Server]]:
    state = Server()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", state
    finally:
        httpd.shutdown()
        httpd.server_close()


BODY = '{"case": {{ case | tojson }}, "mode": {{ config.mode | tojson }}}'


def http_suite(root: Path, url: str, **http: Any) -> Path:
    spec = {"url": url, "body": BODY, "output": "$.data.answer", **http}
    return write_suite(
        root,
        targets=[{"http": spec, "configs": [{"name": "a", "mode": "fast"}]}],
        scorers=["exact_match"],
    )


def test_a_case_goes_out_as_the_rendered_body_and_comes_back_by_json_path(
    tmp_path: Path, server: tuple[str, Server], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, state = server
    monkeypatch.setenv("EVAL_TOKEN", SECRET)
    path = http_suite(
        tmp_path,
        f"{url}/echo",
        headers={"Authorization": "Bearer ${EVAL_TOKEN}"},
        trace="$.meta",
        billing="free",
        versions={"engine": "7"},
    )
    result = run_prepared(path)
    rec = result.record
    assert rec.status == "complete"
    assert rec.summary.metrics["exact_match"].value == 1.0
    sent = sorted(state.requests, key=lambda r: r["case"]["id"])
    assert [r["case"]["id"] for r in sent] == ["q1", "q1", "q2", "q3"]  # q1 twice: warm-up
    assert sent[0]["mode"] == "fast"  # config parameters reach the template
    assert sent[0]["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert sent[0]["headers"]["Content-Type"] == "application/json"
    out = load_run(result.directory).outputs[0]
    assert out.trace.usage is not None and out.trace.usage.output_tokens == 2
    request = rec.target.config["http"]
    assert isinstance(request, dict)
    assert request["headers"] == {"Authorization": "Bearer ${EVAL_TOKEN}"}
    assert rec.target.config["mode"] == "fast"
    assert (rec.target.versions, rec.target.billing) == ({"engine": "7"}, "free")
    for file in result.directory.iterdir():  # the resolved secret is in no record
        assert SECRET not in file.read_text()


@pytest.mark.parametrize(
    ("route", "extra", "kind", "message"),
    [
        ("/status", {}, "http_status", "HTTP 503: upstream down"),
        ("/text", {}, "invalid_output", "JSONDecodeError"),
        ("/echo", {"output": "$.data.missing"}, "invalid_output", "no key 'missing'"),
        ("/echo", {"trace": "$.data"}, "invalid_output", "ValidationError"),
        ("/big", {"max_response_bytes": 1000}, "truncated", "max_response_bytes (1000)"),
        ("/slow", {"timeout_s": 0.2}, "timeout", "http timeout"),
    ],
)
def test_every_failure_kind_is_recorded_and_counted(
    tmp_path: Path,
    server: tuple[str, Server],
    route: str,
    extra: dict[str, Any],
    kind: str,
    message: str,
) -> None:
    url, _ = server
    result = run_prepared(http_suite(tmp_path, f"{url}{route}", **extra))
    got = output_errors(result)
    assert sorted(got) == ["q1", "q2", "q3"]  # every case stays in the denominator
    for case_id, error in got.items():
        assert error is not None, case_id
        assert error[0] == kind
        assert message in error[1]
    assert result.record.summary.metrics["exact_match"].value == 0.0


def test_transport_errors_are_retried(tmp_path: Path, server: tuple[str, Server]) -> None:
    url, state = server
    state.drops = 3  # the untimed warm-up meets the first and is never retried (§18.18)
    sleeps = Sleeps()
    result = run_prepared(http_suite(tmp_path, f"{url}/flaky"), sleeps)
    assert output_errors(result) == {"q1": None, "q2": None, "q3": None}
    assert len(sleeps.calls) == 2  # the other two drops: two backoffs, then answered


def test_the_body_template_is_sandboxed(tmp_path: Path, server: tuple[str, Server]) -> None:
    url, state = server
    body = '{"case": {{ case.__class__.__mro__ }}}'
    result = run_prepared(http_suite(tmp_path, f"{url}/echo", body=body))
    for error in output_errors(result).values():
        assert error is not None and error[0] == "exception"
        assert "SecurityError" in error[1]
    assert state.requests == []  # nothing rendered, nothing sent


def test_config_problems_are_found_before_any_call(
    tmp_path: Path, server: tuple[str, Server]
) -> None:
    url, _ = server
    with pytest.raises(ConfigError, match="EVAL_TOKEN_UNSET"):
        prepare_targets(
            load_suite(http_suite(tmp_path / "a", url, headers={"X-Key": "${EVAL_TOKEN_UNSET}"}))
        )
    with pytest.raises(ConfigError, match="body template"):
        prepare_targets(load_suite(http_suite(tmp_path / "b", url, body="{{ case.id ")))
    with pytest.raises(ConfigError, match=r"only \.key"):
        load_suite(http_suite(tmp_path / "c", url, output="$..deep"))
    with pytest.raises(ConfigError, match="exactly one of python, http, command"):
        load_suite(
            write_suite(
                tmp_path / "d",
                targets=[{"python": "m:f", "http": {"url": url}, "configs": [{"name": "a"}]}],
            )
        )


def test_the_url_is_announced(tmp_path: Path, server: tuple[str, Server]) -> None:
    url, _ = server
    heard: list[str] = []
    prepare_targets(load_suite(http_suite(tmp_path, f"{url}/echo")), announce=heard.append)
    assert heard == [f"config a: POST {url}/echo"]


def test_json_path_subset() -> None:
    reply = {"a": {"b": [{"id": 1}, {"id": 2}]}, "c": [10, 20]}
    assert compile_path("$").select(reply) == reply
    assert compile_path("$.a.b[*].id").select(reply) == [1, 2]
    assert compile_path("$.c[0]").select(reply) == 10
    assert compile_path("$.c[-1]").select(reply) == 20
    with pytest.raises(LookupError, match="no key 'id'"):
        compile_path("$.a.b[*].id").select({"a": {"b": [{"id": 1}, {}]}})  # strict under [*]
    with pytest.raises(LookupError, match="no index 2"):
        compile_path("$.c[2]").select(reply)
    with pytest.raises(LookupError, match=r"\[\*\] on a dict"):
        compile_path("$.a[*]").select(reply)
    for bad in ("a.b", "$.a b", "$['a']", "$..a"):
        with pytest.raises(ConfigError):
            compile_path(bad)


def test_without_its_own_timeout_the_suite_timeout_cancels_the_request(
    tmp_path: Path, server: tuple[str, Server]
) -> None:
    url, _ = server
    path = write_suite(
        tmp_path,
        targets=[
            {"http": {"url": f"{url}/slow", "body": BODY}, "configs": [{"name": "a", "mode": "x"}]}
        ],
        scorers=["exact_match"],
        timeout_s=0.3,
    )
    for error in output_errors(run_prepared(path)).values():
        assert error == ("timeout", "no reply within 0.3s")
