"""The Viewer API (design §10, §18.10): read endpoints, the one write, and the loopback rules."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lite_eval.index import FileCatalog
from lite_eval.runner import RunResult
from lite_eval.server.app import create_app
from lite_eval.store import load_run, set_baseline
from tests.helpers import execute_suite, write_static, wrong

TOKEN = "t" * 32
ORIGIN = "http://127.0.0.1:8720"
CASES = [{"id": f"q{i}", "input": i, "expected": f"a{i}", "tags": {"kind": "k"}} for i in range(4)]


@pytest.fixture
def runs(tmp_path: Path) -> tuple[RunResult, RunResult]:
    (a,) = execute_suite(tmp_path, [("a", wrong("q1"))], cases=CASES)
    (b,) = execute_suite(tmp_path, [("b", wrong("q2", "q3"))], cases=CASES)
    set_baseline(load_run(a.directory))
    return a, b


@pytest.fixture
def static(tmp_path: Path) -> Path:
    return write_static(tmp_path / "static")


@pytest.fixture
def client(tmp_path: Path, runs: tuple[RunResult, RunResult], static: Path) -> Iterator[TestClient]:
    app = create_app(
        tmp_path, catalog=FileCatalog(tmp_path), token=TOKEN, static_dir=static, origins={ORIGIN}
    )
    with TestClient(app, base_url=ORIGIN) as c:
        yield c


def ref(result: RunResult) -> str:
    return f"{result.record.suite.name}/{result.record.run_id}"


def annotation_body(result: RunResult, case_id: str = "q1", **extra: object) -> dict[str, object]:
    return {
        "dataset": "demo",
        "case_id": case_id,
        "case_hash": result.record.case_hashes[case_id],
        "run_id": result.record.run_id,
        "category": "real_miss",
        **extra,
    }


WRITE = {"Origin": ORIGIN, "X-Lite-Eval-Token": TOKEN}


# ---------------------------------------------------------------- reads


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_runs_are_listed_with_the_baseline_marked(
    client: TestClient, runs: tuple[RunResult, RunResult]
) -> None:
    a, b = runs
    body = client.get("/api/runs").json()

    assert body["total"] == 2
    assert [(r["run_id"], r["baseline"]) for r in body["items"]] == [
        (b.record.run_id, False),
        (a.record.run_id, True),
    ]
    assert body["items"][1]["metrics"] == {"exact_match": 0.75}
    filtered = client.get("/api/runs", params={"config": "b", "limit": 1}).json()
    assert filtered["total"] == 1 and filtered["limit"] == 1


def test_run_cases_and_case_detail(client: TestClient, runs: tuple[RunResult, RunResult]) -> None:
    a, b = runs
    detail = client.get(f"/api/runs/{ref(a)}").json()
    assert detail["baseline"] is True and detail["record"]["summary"]["case_weight"] == 0.25

    misses = client.get(f"/api/runs/{ref(b)}/cases", params={"misses_only": "true"}).json()
    assert [r["case_id"] for r in misses["items"]] == ["q2", "q3"]

    case = client.get(f"/api/runs/{ref(b)}/cases/q2").json()
    assert case["content"]["status"] == "current" and case["content"]["case"]["expected"] == "a2"
    assert [o["value"] for o in case["outputs"]] == ["no"]
    assert [o["run_id"] for o in case["other_runs"]] == [a.record.run_id]


def test_compare_datasets_and_trends(client: TestClient, runs: tuple[RunResult, RunResult]) -> None:
    a, b = runs
    c = client.get("/api/compare", params={"baseline": ref(a), "candidate": ref(b)}).json()
    assert [(x["case_id"], x["kind"]) for x in c["changes"]] == [
        ("q2", "regressed"),
        ("q3", "regressed"),
        ("q1", "fixed"),
    ]

    (dataset,) = client.get("/api/datasets").json()
    assert (dataset["name"], dataset["n_cases"]) == ("demo", 4)
    cases = client.get("/api/datasets/demo/cases", params={"limit": 2}).json()
    assert cases["total"] == 4 and [x["case"]["id"] for x in cases["items"]] == ["q0", "q1"]

    trend = client.get(
        "/api/trends", params={"suite": "demo-suite", "metric": "exact_match"}
    ).json()
    assert [p["value"] for p in trend["points"]] == [0.75, 0.5]
    assert [c["run_id"] for c in trend["baseline_changes"]] == [a.record.run_id]


@pytest.mark.parametrize(
    "path",
    [
        "/api/runs/demo-suite/nope",
        "/api/runs/demo-suite/nope/cases",
        "/api/datasets/nope/cases",
        "/api/compare?baseline=demo-suite/x&candidate=demo-suite/y",
    ],
)
def test_unknown_things_are_404(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_malformed_run_reference_is_400(client: TestClient) -> None:
    response = client.get("/api/compare", params={"baseline": "no-slash", "candidate": "a/b"})
    assert response.status_code == 400


# ---------------------------------------------------------------- the one write


def test_an_annotation_is_written_and_read_back(
    client: TestClient, runs: tuple[RunResult, RunResult], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITE_EVAL_AUTHOR", "reviewer-1")
    a, _ = runs
    created = client.post("/api/annotations", json=annotation_body(a, note="n"), headers=WRITE)
    assert created.status_code == 201
    first = created.json()
    assert (first["author"], first["category"], first["note"]) == ("reviewer-1", "real_miss", "n")

    listed = client.get("/api/annotations", params={"dataset": "demo"}).json()
    assert [x["annotation_id"] for x in listed] == [first["annotation_id"]]
    case = client.get(f"/api/runs/{ref(a)}/cases/q1").json()
    assert case["annotation"]["annotation_id"] == first["annotation_id"]

    again = client.post("/api/annotations", json=annotation_body(a), headers=WRITE)
    assert again.status_code == 409 and again.json()["error"]["code"] == "conflict"
    fixed = client.post(
        "/api/annotations",
        json=annotation_body(a, category="label_wrong", supersedes=first["annotation_id"]),
        headers=WRITE,
    )
    assert fixed.status_code == 201


def test_an_invalid_annotation_is_422(
    client: TestClient, runs: tuple[RunResult, RunResult]
) -> None:
    a, _ = runs
    bad = annotation_body(a, category="maybe")
    assert client.post("/api/annotations", json=bad, headers=WRITE).status_code == 422
    too_long = annotation_body(a, note="é" * 3000)  # 6000 bytes, under 16 KB
    assert client.post("/api/annotations", json=too_long, headers=WRITE).status_code == 422


# ---------------------------------------------------------------- §18.10


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "192.168.1.5:8720"])
def test_a_request_for_a_foreign_host_is_refused(client: TestClient, host: str) -> None:
    response = client.get("/api/health", headers={"Host": host})
    assert response.status_code == 403 and response.json()["error"]["code"] == "forbidden_host"


@pytest.mark.parametrize("host", ["localhost:8720", "[::1]:8720", "127.0.0.1"])
def test_loopback_hosts_are_served(client: TestClient, host: str) -> None:
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_a_read_from_a_foreign_origin_is_refused(client: TestClient) -> None:
    response = client.get("/api/runs", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "forbidden_origin"


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({"Origin": ORIGIN}, "missing_token"),
        ({"Origin": ORIGIN, "X-Lite-Eval-Token": "wrong"}, "missing_token"),
        ({"X-Lite-Eval-Token": TOKEN}, "forbidden_origin"),
        ({"Origin": "http://127.0.0.1:9999", "X-Lite-Eval-Token": TOKEN}, "forbidden_origin"),
        ({"Origin": "null", "X-Lite-Eval-Token": TOKEN}, "forbidden_origin"),
    ],
)
def test_a_write_needs_the_viewers_own_origin_and_the_token(
    client: TestClient, runs: tuple[RunResult, RunResult], headers: dict[str, str], code: str
) -> None:
    a, _ = runs
    response = client.post("/api/annotations", json=annotation_body(a), headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == code
    assert client.get("/api/annotations").json() == []


def test_a_write_body_over_16_kb_is_413(client: TestClient) -> None:
    body = b'{"note": "' + b"x" * 17_000 + b'"}'
    sized = client.post(
        "/api/annotations",
        content=body,
        headers={**WRITE, "Content-Type": "application/json"},
    )
    assert sized.status_code == 413

    def chunks() -> Iterator[bytes]:  # no Content-Length: counted while it arrives
        for _ in range(17):
            yield b"x" * 1024

    streamed = client.post(
        "/api/annotations",
        content=chunks(),
        headers={**WRITE, "Content-Type": "application/json"},
    )
    assert streamed.status_code == 413


def test_no_response_carries_a_cors_header(
    client: TestClient, runs: tuple[RunResult, RunResult]
) -> None:
    a, _ = runs
    responses = [
        client.get("/api/runs", headers={"Origin": ORIGIN}),
        client.get("/"),
        client.options(
            "/api/annotations",
            headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
        ),
        client.post("/api/annotations", json=annotation_body(a), headers=WRITE),
        client.get("/api/health", headers={"Host": "evil.example"}),
    ]
    assert [r.status_code for r in responses] == [200, 200, 405, 201, 403]
    for r in responses:
        assert not [h for h in r.headers if h.lower().startswith("access-control-")]


# ---------------------------------------------------------------- the page


def test_the_page_carries_this_process_token(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert f'<meta name="lite-eval-token" content="{TOKEN}"></head>' in page.text
    assert page.headers["cache-control"] == "no-store"
    assert client.get("/assets/index-abc123.js").text == "console.log(1)"


def test_without_a_frontend_build_the_api_still_works(
    tmp_path: Path, runs: tuple[RunResult, RunResult]
) -> None:
    app = create_app(
        tmp_path,
        catalog=FileCatalog(tmp_path),
        token=TOKEN,
        static_dir=tmp_path / "no-build",
        origins={ORIGIN},
    )
    with TestClient(app, base_url=ORIGIN) as c:
        page = c.get("/")
        assert page.status_code == 503 and "build_frontend.py" in page.text
        assert c.get("/api/runs").json()["total"] == 2


def test_a_whole_large_run_can_be_listed(
    client: TestClient, runs: tuple[RunResult, RunResult]
) -> None:
    """No silent cap at 1000 cases: the strip, the table and triage list every case."""
    a, _ = runs
    response = client.get(f"/api/runs/{ref(a)}/cases", params={"limit": 50_000})
    assert response.status_code == 200 and response.json()["limit"] == 50_000


def test_the_export_format_is_in_the_spec() -> None:
    """The frontend types an exported page from the spec, not by hand."""
    import json

    spec = json.loads((Path(__file__).parents[2] / "openapi.json").read_text())
    payload = spec["components"]["schemas"]["ExportPayload"]
    assert set(payload["required"]) >= {"kind", "runs", "comparison", "start"}
    assert "ExportedRun" in spec["components"]["schemas"]
