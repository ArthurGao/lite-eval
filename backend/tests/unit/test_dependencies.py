"""Pin the third-party APIs later stages rely on, at the exact versions in pyproject.toml.

Each test is a minimal call with a hand-computed expected value. An upgrade that changes one of
these APIs fails here first. Versions and notes: project KB reference/dependencies.md.
"""

import json

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    case_id: str


def test_pydantic_round_trip_frozen_and_forbid_extra() -> None:
    record = _Record.model_validate_json('{"case_id": "q1"}')

    assert _Record.model_validate_json(record.model_dump_json()) == record
    assert record.schema_version == 1
    with pytest.raises(ValidationError):
        record.case_id = "q2"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _Record.model_validate_json('{"case_id": "q1", "unknown": 1}')


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LITE_EVAL_")

    home: str = "default"


class _AliasedSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LITE_EVAL_")

    home: str = Field(default="default", alias="home")


def test_pydantic_settings_reads_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITE_EVAL_HOME", "/tmp/evals")

    assert _Settings().home == "/tmp/evals"


def test_pydantic_settings_alias_ignores_the_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    # Design §3 L28: an alias replaces the prefixed name, so settings fields never use aliases.
    monkeypatch.setenv("LITE_EVAL_HOME", "/tmp/evals")
    monkeypatch.delenv("HOME", raising=False)

    assert _AliasedSettings().home == "default"


def test_jinja2_sandbox_renders_case_and_blocks_dunder_access() -> None:
    from jinja2.exceptions import SecurityError
    from jinja2.sandbox import SandboxedEnvironment

    env = SandboxedEnvironment()

    body = env.from_string('{"q": {{ case.input.query | tojson }}}').render(
        case={"input": {"query": "什么是 RRF？"}}
    )
    assert json.loads(body) == {"q": "什么是 RRF？"}
    with pytest.raises(SecurityError):
        env.from_string("{{ case.__class__.__mro__ }}").render(case={})


def test_yaml_safe_load_rejects_python_tags() -> None:
    import yaml

    assert yaml.safe_load("gate:\n  - {metric: hit@5, min: 0.9}") == {
        "gate": [{"metric": "hit@5", "min": 0.9}]
    }
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.safe_load("!!python/object/apply:os.system ['true']")


async def test_httpx2_async_client_with_mock_transport_and_timeout() -> None:
    import httpx2

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"echo": json.loads(request.content)["q"]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        response = await client.post("http://target.test/run", json={"q": "hello"})

    assert response.json() == {"echo": "hello"}
    timeout = httpx2.Timeout(120.0, connect=5.0)
    assert (timeout.connect, timeout.read) == (5.0, 120.0)


def test_fastapi_test_client_runs_on_httpx2() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    api = FastAPI()

    @api.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    assert TestClient(api).get("/api/health").json() == {"status": "ok"}


def test_lockfile_has_one_http_client() -> None:
    # One HTTP client per process: nothing in the locked environment may pull plain httpx.
    # Our own code is kept off httpx by ruff's banned-api (TID251).
    import tomllib
    from pathlib import Path

    lock = tomllib.loads((Path(__file__).parents[2] / "uv.lock").read_text())
    names = {package["name"] for package in lock["package"]}

    assert "httpx2" in names
    assert "httpx" not in names


# Hand-computed: q1 hit at rank 1, q2 at rank 2, q3 missed.
#   hit_rate@1 = 1/3, hit_rate@2 = 2/3, recall@2 = 2/3, mrr = (1 + 1/2 + 0) / 3 = 0.5,
#   ndcg@2 = (1 + 1/log2(3) + 0) / 3.
_QRELS = {"q1": {"d1": 1}, "q2": {"d2": 1}, "q3": {"d9": 1}}
_RUN = {
    "q1": {"d1": 0.9, "d2": 0.5},
    "q2": {"d1": 0.9, "d2": 0.8},
    "q3": {"d1": 0.9, "d2": 0.8},
}


def test_ranx_metrics_match_hand_computed_values() -> None:
    import math

    from ranx import Qrels, Run, evaluate

    scores = evaluate(
        Qrels.from_dict(_QRELS),
        Run.from_dict(_RUN),
        ["hit_rate@1", "hit_rate@2", "recall@2", "mrr", "ndcg@2"],
    )

    assert scores["hit_rate@1"] == pytest.approx(1 / 3, abs=1e-12)
    assert scores["hit_rate@2"] == pytest.approx(2 / 3, abs=1e-12)
    assert scores["recall@2"] == pytest.approx(2 / 3, abs=1e-12)
    assert scores["mrr"] == pytest.approx(0.5, abs=1e-12)
    assert scores["ndcg@2"] == pytest.approx((1 + 1 / math.log2(3)) / 3, abs=1e-12)


def test_scipy_binomtest_matches_the_exact_mcnemar_formula() -> None:
    from scipy.stats import binomtest

    # Design §18.2: p = min(1, 2 * BinomCDF(min(b, c); b + c, 0.5)); b=1, c=9 -> 2 * 11/1024.
    assert binomtest(1, 10, 0.5).pvalue == pytest.approx(22 / 1024, abs=1e-15)


def test_openai_sdk_timeout_type_is_its_own() -> None:
    # KB global/llm-evals/providers/openai.md: the SDK runs on httpx2;
    # build timeouts with openai.Timeout.
    import httpx2
    import openai

    assert openai.Timeout is httpx2.Timeout
