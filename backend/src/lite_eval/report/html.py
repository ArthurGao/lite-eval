"""`lite-eval report --html`: one self-contained HTML file (design §10.2, §18.12).

The page is the Viewer's own build with its data embedded, so it opens from disk with no server:
- the stylesheet goes into a <style> element;
- the module script becomes a `data:` URL, so any `</script>` text inside the bundle cannot end a
  tag (the build has one JS file: the frontend disables code splitting);
- the data goes into <script type="application/json" id="lite-eval-data"> with `<`, `>`, `&`,
  U+2028 and U+2029 written as \\uXXXX, so case text such as `</script>` stays data.

Redaction (§18.12): the union of the exported runs' recorded rules plus `--redact` rules, applied
by `views` to outputs, scores, warnings and target config -- so one run's text also passes the
other run's rules in a comparison. Rules can be added, never removed; ids, hashes and dataset
content are never rewritten.
"""

import base64
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lite_eval import __version__
from lite_eval.errors import ConfigError
from lite_eval.index import run_row
from lite_eval.server.paths import NO_FRONTEND
from lite_eval.store import load_run, read_record, root_of
from lite_eval.views import (
    CasePage,
    Notes,
    compare_loaded,
    detail,
    open_run,
    rows,
    run_detail,
    union,
)

_SCRIPT = re.compile(r'<script type="module"[^>]*\ssrc="/?([^"]+\.js)"[^>]*></script>')
_STYLE = re.compile(r'<link rel="stylesheet"[^>]*\shref="/?([^"]+\.css)"[^>]*>')
_ESCAPES = {
    "<": "\\u003c",
    ">": "\\u003e",
    "&": "\\u0026",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}


def embed_json(data: Any) -> str:
    """JSON safe inside a <script> element (§18.12)."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return re.sub("[<>&\u2028\u2029]", lambda m: _ESCAPES[m.group()], text)


def _payload(directories: Sequence[Path], extra_rules: Sequence[str]) -> dict[str, Any]:
    """Each run loaded once; every case built from that load, with the union of the runs'
    rules plus `extra_rules`."""
    records = [read_record(d) for d in directories]
    redactor = union(records, extra_rules)
    exported = [
        (root_of(d), r.suite.name, r.run_id) for d, r in zip(directories, records, strict=True)
    ]
    candidates = [run_row(r) for r in records]  # a case's other runs: only the exported ones
    runs: dict[str, Any] = {}
    for root, suite, run_id in exported:
        view = open_run(root, suite, run_id, redactor)
        notes = Notes.load(root)
        case_rows = rows(view, notes)
        runs[f"{suite}/{run_id}"] = {
            "detail": run_detail(root, suite, run_id, redactor).model_dump(mode="json"),
            "rows": CasePage(
                items=case_rows, total=len(case_rows), offset=0, limit=len(case_rows)
            ).model_dump(mode="json"),
            "cases": {
                r.case_id: detail(view, r.case_id, notes, candidates).model_dump(mode="json")
                for r in case_rows
            },
        }
    compared = None
    if len(directories) == 2:
        a, b = (load_run(d, outputs=False) for d in directories)  # each from its own root
        compared = compare_loaded(a, b, redactor).model_dump(mode="json")
    return {
        "kind": "compare" if compared else "run",
        "generated_at": datetime.now(UTC).isoformat(),
        "lite_eval_version": __version__,
        "runs": runs,
        "comparison": compared,
    }


def render(static_dir: Path, directories: Sequence[Path], extra_rules: Sequence[str] = ()) -> str:
    """The exported page for one run, or a comparison of two (baseline first)."""
    index = static_dir / "index.html"
    if not index.is_file():
        raise ConfigError(f"nothing to export: {NO_FRONTEND}")
    data = _payload(directories, extra_rules)
    html = index.read_text(encoding="utf-8")

    def script(match: re.Match[str]) -> str:
        code = base64.b64encode((static_dir / match.group(1)).read_bytes()).decode()
        return f'<script type="module" src="data:text/javascript;base64,{code}"></script>'

    def style(match: re.Match[str]) -> str:
        css = (static_dir / match.group(1)).read_text(encoding="utf-8")
        return f"<style>{css.replace('</style', '<\\/style')}</style>"

    html, scripts = _SCRIPT.subn(script, html)
    html = _STYLE.sub(style, html)
    if scripts != 1:
        raise ConfigError(f"{index}: expected one module script, found {scripts}; rebuild it")
    block = f'<script type="application/json" id="lite-eval-data">{embed_json(data)}</script>'
    return html.replace("</head>", block + "</head>", 1)
