# lite-eval

A general evaluation module for AI applications: RAG, memory, extraction, judging and grounded writing. It answers
one question for any system you can call: **did this change make it better or worse, and can you trust that?**

- **Any system under test:** a Python object, an HTTP service or a subprocess, declared in a suite.
- **Generate once, score many:** outputs are cached by target config and case content; changing a scorer never
  calls the system again (`rescore`).
- **Deterministic scorers first, calibrated model judges second:** a judge is a scorer only after a calibration
  run shows how well it agrees with labels, and its model, prompt, temperature and thinking setting are recorded.
- **Immutable run records** with full provenance: target config, versions, dataset hash, scorer versions, git sha,
  cost. Errors and timeouts stay in the denominator.
- **Honest comparisons:** paired, case by case, with significance; a difference that is not significant is never
  reported as one. Gates exit non-zero in CI.
- **A local Viewer** for runs, comparisons, every case's trace, miss triage, datasets and trends.

Status: v0.3 (2026-11-18). Used by [lite-rag](https://github.com/ArthurGao/lite-rag) (retrieval, grounded-answer
judge), [lite-mem](https://github.com/ArthurGao/lite-mem) (memory, LongMemEval) and llm-steward (a recall gate).
Design and decisions: [`docs/lite-eval-design.md`](docs/lite-eval-design.md).

## Install

lite-eval is not on PyPI. Each release attaches a wheel (with the Viewer built in) to its
[GitHub Release](https://github.com/ArthurGao/lite-eval/releases); depend on it by URL:

```toml
# pyproject.toml
[tool.uv.sources]
lite-eval = { url = "https://github.com/ArthurGao/lite-eval/releases/download/v0.3.1/lite_eval-0.3.1-py3-none-any.whl" }
```

Python 3.12. Installing from a git commit also works, but without the Viewer (its build is not in git).

## A first run

A suite names a dataset, the target configs to compare and the scorers. This one is in `backend/examples/`:

```yaml
# examples/suites/demo.yaml
schema_version: 1
name: capitals-demo
dataset: ../datasets/capitals          # dataset.yaml + cases.jsonl (+ corpus/)
targets:
  - python: examples.demo_target:make  # a factory returning the system under test
    configs:
      - {name: strict}
      - {name: fuzzy, fuzzy: true}
scorers:
  - exact_match
  - {name: contains, case_sensitive: false}
```

```
cd backend
uv run lite-eval run examples/suites/demo.yaml          # one run directory per config, under examples/runs/
uv run lite-eval compare examples/runs/capitals-demo/<strict run> examples/runs/capitals-demo/<fuzzy run>
```

The comparison says what it can and cannot claim:

```
exact_match  0.667 -> 1.000  (+0.3333)  not significant (n=3, 1 discordant case; mcnemar p=1)
cases: 0 regressed, 2 fixed, 0 changed (worse first)
```

A target is any object with `run(case) -> Output` plus `config()`, `versions()` and `billing()`
(`backend/examples/demo_target.py` is a complete one). HTTP and subprocess targets need no Python code in the
suite's repo (design §18.11).

## Commands

| Command | What it does |
| --- | --- |
| `lite-eval run <suite> [--config NAME] [--no-cache] [--gate] [--ci]` | generate, score, write one run per config |
| `lite-eval rescore <run> [--scorer SPEC]` | score stored outputs again; never calls the target |
| `lite-eval compare <baseline run> <candidate run>` | paired deltas with significance, the cases that changed |
| `lite-eval baseline set <run>` | the run a suite compares and gates against |
| `lite-eval view` | the Viewer on `http://127.0.0.1:8720` (loopback only) |
| `lite-eval triage <run>` | classify a run's misses (real miss, label wrong, excluded, unclear) |
| `lite-eval report <run>\|<base>..<cand> --html -o <file>` | one self-contained HTML page of a run or a comparison |
| `lite-eval dataset import longmemeval\|locomo <file> <dir>` | an official benchmark file as a dataset |
| `lite-eval export kb <runs…>` | rows for a knowledge base's model-eval results log |

Exit codes: 0 pass, 1 gate failed, 2 configuration or input error, 3 run failed.

From Python (e.g. a pytest gate): `run_suite`, `rescore_run`, `compare_runs`, `assert_gate` from `lite_eval`.

## Packs

A pack sets a task type's expected shape, default scorers and metrics: `retrieval` (hit@k, MRR, nDCG, recall,
document hits; cross-checked against ranx), `memory`, `extraction` (item recall, duplicates), `grounded` (must / must not,
plus a grounded judge) and `judging` (a judge as the system under test: agreement with labels, per construction and
as a matrix). Model judges run on local Ollama or any OpenAI-compatible endpoint; CI refuses anything not declared
`free` unless explicitly allowed.

## Development

```
backend/   Python package (uv): uv sync --locked --all-extras; uv run pytest; uv run ruff check; uv run mypy
frontend/  Viewer SPA (pnpm): pnpm install; pnpm typecheck && pnpm lint && pnpm test && pnpm build && pnpm e2e
```

Tests never call a real model; local models only under `pytest -m ollama`. The agent guide is
[`AGENTS.md`](AGENTS.md).

License: [Apache-2.0](LICENSE).
