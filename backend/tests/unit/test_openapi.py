"""The committed OpenAPI spec is the frontend's only contract (frontend AGENTS rule 1)."""

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def test_openapi_spec_is_current() -> None:
    done = subprocess.run(
        [sys.executable, "scripts/dump_openapi.py", "--check"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_spec_lists_every_viewer_route() -> None:
    import json

    paths = set(json.loads((BACKEND / "openapi.json").read_text())["paths"])
    assert paths == {
        "/api/health",
        "/api/runs",
        "/api/runs/{suite}/{run_id}",
        "/api/runs/{suite}/{run_id}/cases",
        "/api/runs/{suite}/{run_id}/cases/{case_id}",
        "/api/compare",
        "/api/datasets",
        "/api/datasets/{name}/cases",
        "/api/trends",
        "/api/annotations",
    }
