"""The Viewer's end-to-end fixture (frontend `pnpm e2e`; design §16 E3 acceptance).

Builds an evaluation root from real runs of the fake target (no model, no network), writes the
two HTML exports, then serves `lite-eval view` on it:

    uv run python -m tests.e2e.viewer_fixture \
        --root <dir> --exports <dir> --static <dist> --port 8722

What the root holds (the Playwright specs rely on it):
- dataset `golden` with 12 cases; q01 carries `</script><script>window.__pwned=1</script>`;
- suite `demo`: run `baseline` (misses q03, q07), then `candidate` (misses q03, q05, q09, q11:
  q05, q09 and q11 regressed, q07 fixed); the baseline is set to `baseline`, then switched to
  `candidate`;
- `candidate` again on dataset version 2 (q02's expected changed), for the trend's dataset mark;
- `candidate`'s q11 output leaks a key-shaped string (and so misses); the suite redacts
  `api_keys`, so the Viewer and the exports show it redacted.
"""

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from lite_eval.report.html import render
from lite_eval.store import load_run, set_baseline
from lite_eval.targets.fake import FakeTarget, Step
from tests.helpers import execute_one, wrong

SCRIPT = "</script><script>window.__pwned=1</script>"
LEAK = "sk-" + "E2E" * 8


def cases(version: int) -> list[dict[str, Any]]:
    out = []
    for i in range(1, 13):
        cid = f"q{i:02d}"
        query = f"question {i} {SCRIPT}" if cid == "q01" else f"question {i}"
        expected = "a2-revised" if (cid == "q02" and version == 2) else f"a{i}"
        out.append(
            {
                "id": cid,
                "input": {"query": query},
                "expected": expected,
                "tags": {"kind": "keyword" if i % 2 else "paraphrase"},
            }
        )
    return out


def target(misses: list[str], leak: str | None = None) -> FakeTarget:
    """tests.helpers.wrong, plus one output that leaks a key (and so misses). The leak is part
    of the config, which keys the output cache."""
    if leak is None:
        return wrong(*misses)
    steps = {**wrong(*misses).steps, leak: Step(value=f"answer with {LEAK}")}
    return FakeTarget(steps=steps, config={"wrong": sorted(misses), "leak": leak})


def build(root: Path) -> tuple[Path, Path]:
    """The root's runs; returns (baseline run, candidate run on dataset v1)."""
    spec = {"name": "demo", "redact": ["api_keys"], "dataset_meta": {"name": "golden"}}
    base = execute_one(root, "baseline", target(["q03", "q07"]), cases=cases(1), **spec)
    cand = execute_one(
        root, "candidate", target(["q03", "q05", "q09"], leak="q11"), cases=cases(1), **spec
    )
    execute_one(
        root,
        "candidate",
        target(["q03", "q05", "q09"]),
        cases=cases(2),
        **{**spec, "dataset_meta": {"name": "golden", "version": "2"}},
    )
    set_baseline(load_run(base.directory))
    set_baseline(load_run(cand.directory))
    return base.directory, cand.directory


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--exports", type=Path, required=True)
    parser.add_argument("--static", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8722)
    args = parser.parse_args(argv)

    if args.root.exists():
        shutil.rmtree(args.root)  # a fresh root every e2e run: annotations start empty
    args.root.mkdir(parents=True)
    baseline, candidate = build(args.root)
    args.exports.mkdir(parents=True, exist_ok=True)
    (args.exports / "run.html").write_text(render(args.static, [candidate]), encoding="utf-8")
    (args.exports / "compare.html").write_text(
        render(args.static, [baseline, candidate]), encoding="utf-8"
    )

    from lite_eval.server.run import serve

    print(f"viewer fixture: {args.root} on 127.0.0.1:{args.port}", flush=True)
    serve(args.root, "127.0.0.1", args.port, args.static)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
