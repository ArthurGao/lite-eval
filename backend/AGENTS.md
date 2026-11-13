# backend — Agent Guide

The Python package `lite_eval`: contracts, datasets, targets, scorers, packs, runner, store, analysis, reports, CLI,
pytest plugin, and the Viewer's API. Read the root `AGENTS.md` first; this file adds backend rules.
Design: `docs/lite-eval-design.md` §5 (contracts), §6 (runner), §7 (packs), §8 (analysis), §9 (judges),
§11 (interfaces), §12 (storage), §15 (layout).

## Layout (create in E0/E1)

```
backend/
  pyproject.toml              # uv; extras: [server] (fastapi, uvicorn), [ranking] (ranx, scipy), [judge] (openai)
  src/lite_eval/
    canonical.py              # canonical JSON + sha256 (design §18.6); every hash goes through it
    contracts.py              # every contract, pydantic, schema_version; imports only canonical
    errors.py                 # ConfigError (exit 2), RunError (exit 3)
    config.py                 # pydantic-settings, prefix LITE_EVAL_, no field aliases (design §3 L28)
    suite.py                  # suite YAML and the evaluation root (design §18.20)
    datasets/                 # local.py: dataset dir, hashes, secret scan; loaders/ (longmemeval, locomo)
    targets/                  # base.py (protocol, errors), python.py, fake.py; http.py, command.py (E4)
    scorers/                  # base.py, deterministic.py, compose.py, ranking.py (hit/mrr/ndcg@k); judge.py (E5)
    packs/                    # __init__.py: registry (retrieval); cost.py comes with every run
    cache.py                  # output cache (design §18.5)
    runner.py                 # two-phase execution (design §6)
    store.py  index.py        # run directories (truth) and the Viewer's SQLite index (rebuildable)
    annotations.py            # append-only annotations under a file lock (design §18.9)
    views.py                  # what the Viewer and the HTML export show (design §18.22)
    analysis/                 # summary, compare (McNemar, bootstrap, comparability), gate
    api.py                    # compare_runs, gate_results, assert_gate (design §11.3)
    report/                   # terminal, html, kb
    redact.py
    server/                   # FastAPI: guard.py (§18.10), app.py (routes), run.py (view, --check),
                              # paths.py; static/ = frontend build (copied, gitignored)
    prompts/*.j2
    cli.py  pytest_plugin.py
  tests/
    helpers.py                # dataset and suite builders under tmp_path
    data/records/v1/          # committed run directory of schema_version 1 (must keep loading)
    unit/  integration/  e2e/ # e2e: real subprocess and HTTP targets against local fake servers
  examples/                   # one minimal target per access method + a tiny dataset
  scripts/dump_openapi.py     # writes openapi.json, the frontend's contract (`--check` runs in pytest)
  openapi.json                # committed; regenerate after any route or response model change
```

FakeTarget lives in the package (`lite_eval/targets/fake.py`) so suites and examples can use it too;
FakeJudge joins it in E5.

## Layering

- `contracts` imports nothing else from the package except `canonical`. `analysis/` and `report/terminal.py` read
  contracts only; `report/html.py` reads run directories through `views`. None of them calls a target or a judge.
- `runner` is the only module that calls targets; `scorers/judge.py` is the only module that calls a model.
- `server/` reads through `views`, `store` and `index`; it never runs a suite. Its only write is an annotation
  (`annotations.append_annotation`, shared with `lite-eval triage`).
- `cli.py` and `pytest_plugin.py` are thin: they parse arguments and call the public API in `__init__.py`.

## Rules

1. **Contracts first.** A new field needs a default (old records stay readable) and a round-trip test that loads a
   record written by the previous `schema_version`. Keep committed sample records under `tests/data/records/`.
2. **The cache key is everything that decides the output** (design §18.5): target config (incl. thinking / effort),
   `versions()` (engine, model snapshot, prompt version), case hash, repeat index. A value that changes the output but
   is missing from `config()` / `versions()` is a bug. Hashing is canonical JSON (§18.6), everywhere.
3. **Statistics are tested against hand-computed examples** (McNemar exact p-values, bootstrap with a fixed seed).
   Ranking metrics are cross-checked against ranx in a test, the way lite-rag did.
4. **Failure paths are first-class.** Every target access method has tests for timeout, exception, truncated and
   unparsable output, and a budget stop that leaves a `partial` run.
5. **Retries**: transport errors only, exponential backoff with a capped exponent (design §3 L31), at most 3.
6. **Time budgets in tests are generous** (10 s default); tests about timeouts set their own (L32).
7. **The Viewer API binds loopback only** and refuses to start otherwise; test it like llm-gateway does.
8. Typed throughout (`mypy --strict` on `src/`); async where a target or a judge is called.
9. **Design §18 is the tie-breaker**: repeats, statistics, comparability, exit codes, failures, annotations, Viewer
   security, HTTP/subprocess targets, redaction, the CI billing guard, packaging, the index, setup sharing and gate
   semantics are specified there.

## Commands

```
uv sync --locked --all-extras
uv run pytest
uv run pytest -m ollama
uv run ruff check && uv run ruff format --check && uv run mypy
uv run lite-eval --help
uv run python scripts/dump_openapi.py   # after a server route or schema change
```
