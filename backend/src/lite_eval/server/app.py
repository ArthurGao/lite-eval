"""The Viewer's FastAPI app (design §10.2): read-only endpoints over the run directories, the
annotation write, and the built single-page app with this process's token in it.

Every route reads through `views` and the run catalog; the only write is an annotation.
"""

from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from lite_eval import __version__
from lite_eval.annotations import AnnotationConflict, annotate, read_annotations
from lite_eval.contracts import Annotation, AnnotationCategory, Comparison, Note, RunStatus
from lite_eval.errors import ConfigError, NotFound
from lite_eval.index import RunCatalog, RunFilter, RunPage, Trend
from lite_eval.server.guard import Guard, error_body
from lite_eval.server.paths import NO_FRONTEND
from lite_eval.views import (
    CaseDetail,
    CasePage,
    DatasetCasePage,
    DatasetSummary,
    RunDetail,
    case_detail,
    case_page,
    comparison,
    dataset_cases,
    datasets,
    run_detail,
)


class Health(BaseModel):
    status: Literal["ok"] = "ok"


class AnnotationRequest(BaseModel):
    """What the page sends; the server adds the id, the author and the time (design §18.9)."""

    model_config = ConfigDict(extra="forbid")

    dataset: str
    case_id: str
    case_hash: str
    run_id: str | None = None
    category: AnnotationCategory
    note: Note = ""
    supersedes: str | None = None


def _run_ref(value: str) -> tuple[str, str]:
    suite, sep, run_id = value.partition("/")
    if not sep or not suite or not run_id or "/" in run_id:
        raise ConfigError(f"a run is named <suite>/<run_id>, got {value!r}")
    return suite, run_id


def _error(status: int, message: str, code: str) -> Response:
    return Response(error_body(message, code), status_code=status, media_type="application/json")


def create_app(
    root: Path,
    *,
    catalog: RunCatalog,
    token: str,
    static_dir: Path | None,
    origins: set[str],
) -> FastAPI:
    app = FastAPI(
        title="lite-eval Viewer",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(Guard, origins=origins, token=token)

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> Response:
        return _error(404, str(exc), "not_found")

    @app.exception_handler(AnnotationConflict)
    async def _conflict(_: Request, exc: AnnotationConflict) -> Response:
        return _error(409, str(exc), "conflict")

    @app.exception_handler(ConfigError)
    async def _bad(_: Request, exc: ConfigError) -> Response:
        return _error(400, str(exc), "bad_request")

    @app.get("/api/health", response_model=Health)
    def health() -> Health:
        return Health()

    @app.get("/api/runs", response_model=RunPage)
    def list_runs(
        suite: str | None = None,
        config: str | None = None,
        status: RunStatus | None = None,
        pack: str | None = None,
        dataset: str | None = None,
        dataset_version: str | None = None,
        baseline: bool | None = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> RunPage:
        filters = RunFilter(
            suite=suite,
            config=config,
            status=status,
            pack=pack,
            dataset=dataset,
            dataset_version=dataset_version,
            baseline=baseline,
        )
        return catalog.list_runs(filters, offset=offset, limit=limit)

    @app.get("/api/runs/{suite}/{run_id}", response_model=RunDetail)
    def get_run(suite: str, run_id: str) -> RunDetail:
        return run_detail(root, suite, run_id)

    @app.get("/api/runs/{suite}/{run_id}/cases", response_model=CasePage)
    def list_cases(
        suite: str,
        run_id: str,
        misses_only: bool = False,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> CasePage:
        return case_page(root, suite, run_id, misses_only=misses_only, offset=offset, limit=limit)

    @app.get("/api/runs/{suite}/{run_id}/cases/{case_id}", response_model=CaseDetail)
    def get_case(suite: str, run_id: str, case_id: str) -> CaseDetail:
        return case_detail(root, suite, run_id, case_id, catalog)

    @app.get("/api/compare", response_model=Comparison)
    def compare_runs(baseline: str, candidate: str) -> Comparison:
        return comparison(root, _run_ref(baseline), _run_ref(candidate))

    @app.get("/api/datasets", response_model=list[DatasetSummary])
    def list_datasets() -> list[DatasetSummary]:
        return datasets(root)

    @app.get("/api/datasets/{name}/cases", response_model=DatasetCasePage)
    def list_dataset_cases(
        name: str,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> DatasetCasePage:
        return dataset_cases(root, name, offset=offset, limit=limit)

    @app.get("/api/trends", response_model=Trend)
    def trend(suite: str, metric: str, config: str | None = None) -> Trend:
        return catalog.trend(suite, metric, config=config)

    @app.get("/api/annotations", response_model=list[Annotation])
    def list_annotations(
        dataset: str | None = None, case_id: str | None = None
    ) -> list[Annotation]:
        return [
            a
            for a in read_annotations(root)
            if dataset in (None, a.dataset) and case_id in (None, a.case_id)
        ]

    @app.post("/api/annotations", response_model=Annotation, status_code=201)
    def create_annotation(body: AnnotationRequest) -> Annotation:
        return annotate(root, **body.model_dump())

    index = static_dir / "index.html" if static_dir else None

    @app.get("/", include_in_schema=False)
    def page() -> Response:
        if index is None or not index.is_file():
            return HTMLResponse(NO_FRONTEND, status_code=503)
        html = index.read_text(encoding="utf-8").replace(
            "</head>", f'<meta name="lite-eval-token" content="{token}"></head>', 1
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    if static_dir is not None and (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/api/{rest:path}", include_in_schema=False)
    def unknown(rest: str) -> Response:
        return _error(404, f"no endpoint /api/{rest}", "not_found")

    return app
