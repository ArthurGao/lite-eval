"""Write the Viewer API's OpenAPI spec to backend/openapi.json, the frontend's only contract
(frontend `pnpm generate-client` reads it). `--check` exits 1 when the file is stale; the test
suite runs the check, so a route change without a regenerated spec fails CI."""

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

SPEC = Path(__file__).resolve().parents[1] / "openapi.json"


def _normalise(node: Any) -> Any:
    """Drop an `enum` that only repeats `const`: pydantic 2.9 (the declared floor) writes
    {"const": "ok", "enum": ["ok"]} for a one-value Literal, 2.14 (locked) only the const. The
    two mean the same, so the committed spec checks out under both (CI job `lowest`)."""
    if isinstance(node, list):
        return [_normalise(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {k: _normalise(v) for k, v in node.items()}
    if "const" in out and out.get("enum") == [out["const"]]:
        del out["enum"]
    return out


def spec() -> str:
    from lite_eval.index import FileCatalog
    from lite_eval.server.app import create_app

    with tempfile.TemporaryDirectory() as root:
        app = create_app(
            Path(root), catalog=FileCatalog(Path(root)), token="-", static_dir=None, origins=set()
        )
        return json.dumps(_normalise(app.openapi()), indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    current = spec()
    if "--check" in argv:
        if not SPEC.exists() or SPEC.read_text(encoding="utf-8") != current:
            print(f"{SPEC} is stale: run `uv run python scripts/dump_openapi.py`")
            return 1
        return 0
    SPEC.write_text(current, encoding="utf-8")
    print(f"wrote {SPEC}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
