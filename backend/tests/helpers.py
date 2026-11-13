"""Builders shared by tests: dataset directories and suite files under tmp_path."""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from lite_eval.runner import RunOptions, RunResult
    from lite_eval.targets.base import Target
    from lite_eval.targets.fake import FakeTarget


def write_dataset(
    root: Path,
    cases: list[dict[str, Any]],
    *,
    name: str = "demo",
    version: str = "1",
    pack: str | None = None,
    corpus: dict[str, bytes] | None = None,
    **meta: Any,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    header: dict[str, Any] = {"name": name, "version": version, **meta}
    if pack is not None:
        header["pack"] = pack
    (root / "dataset.yaml").write_text(yaml.safe_dump(header, allow_unicode=True), encoding="utf-8")
    (root / "cases.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8"
    )
    for rel, data in (corpus or {}).items():
        path = root / "corpus" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


DEFAULT_CASES: list[dict[str, Any]] = [
    {
        "id": "q1",
        "input": {"query": "capital of NZ"},
        "expected": "Wellington",
        "tags": {"kind": "fact"},
    },
    {"id": "q2", "input": {"query": "2+2"}, "expected": "4", "tags": {"kind": "math"}},
    {"id": "q3", "input": {"query": "colour of sky"}, "expected": "blue", "tags": {"kind": "fact"}},
]


def write_suite(
    root: Path,
    *,
    cases: list[dict[str, Any]] | None = None,
    dataset_meta: dict[str, Any] | None = None,
    **spec: Any,
) -> Path:
    """An evaluation root at `root`: suites/s.yaml and datasets/demo. Returns the suite path."""
    write_dataset(root / "datasets" / "demo", cases or DEFAULT_CASES, **(dataset_meta or {}))
    body: dict[str, Any] = {
        "name": "demo-suite",
        "dataset": "../datasets/demo",
        "targets": [{"python": "lite_eval.targets.fake:from_config", "configs": [{"name": "a"}]}],
        "scorers": ["exact_match"],
        **spec,
    }
    suite = root / "suites" / "s.yaml"
    suite.parent.mkdir(parents=True, exist_ok=True)
    suite.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return suite


def hit(doc: str, text: str = "", hid: str | None = None) -> dict[str, Any]:
    """One retrieval hit as a target returns it."""
    return {"doc": doc, "text": text} | ({"id": hid} if hid else {})


def execute_suite(
    root: Path,
    targets: "list[tuple[str, Target]]",
    options: "RunOptions | None" = None,
    **spec: Any,
) -> "list[RunResult]":
    """Write an evaluation root (write_suite) and run the given targets on it in-process."""
    import asyncio

    from lite_eval.datasets.local import load_dataset
    from lite_eval.runner import RunOptions, execute
    from lite_eval.suite import load_suite

    suite = load_suite(write_suite(root, **spec))
    dataset = load_dataset(suite.dataset_path)
    return asyncio.run(execute(suite, dataset, targets, options or RunOptions()))


def wrong(*ids: str) -> "FakeTarget":
    """A fake that answers every case right except `ids`."""
    from lite_eval.targets.fake import FakeTarget, Step

    # The config names the behaviour: two different fakes must not share cache entries.
    return FakeTarget(
        steps={i: Step(value="no", echo_expected=False) for i in ids},
        config={"wrong": sorted(ids)},
    )


def execute_one(root: Path, config: str, target: "Target", **spec: Any) -> "RunResult":
    """execute_suite with one target config; returns its run."""
    (result,) = execute_suite(root, [(config, target)], **spec)
    return result


def write_static(directory: Path, js: str = "console.log(1)") -> Path:
    """A stand-in for the frontend build, in Vite's shape: index.html + one JS + one CSS."""
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text(
        "<!doctype html><html><head><title>lite-eval</title>"
        '<script type="module" crossorigin src="/assets/index-abc123.js"></script>'
        '<link rel="stylesheet" crossorigin href="/assets/index-def456.css">'
        '</head><body><div id="root"></div></body></html>'
    )
    (directory / "assets" / "index-abc123.js").write_text(js)
    (directory / "assets" / "index-def456.css").write_text("body{color:#111}")
    return directory
