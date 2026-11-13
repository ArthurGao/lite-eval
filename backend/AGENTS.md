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
    contracts.py              # every contract, pydantic, schema_version
    config.py                 # pydantic-settings, prefix LITE_EVAL_, no field aliases (design §3 L28)
    datasets/                 # local dir + hash; loaders/ (longmemeval, locomo)
    targets/                  # python.py, http.py, command.py, fake.py
    scorers/                  # deterministic.py, ranking.py, judge.py, compose.py
    packs/                    # one module per task type
    runner.py                 # two-phase execution (design §6)
    store.py  index.py        # run directories (truth) and the Viewer's SQLite index (rebuildable)
    analysis/                 # summary, compare (McNemar, bootstrap), gate
    report/                   # terminal, html, kb
    redact.py
    server/                   # FastAPI: read-only API + annotations; static/ = frontend build (copied, gitignored)
    prompts/*.j2
    cli.py  pytest_plugin.py
  tests/
    fakes/                    # FakeTarget (scripted outputs, errors, latency), FakeJudge
    unit/  integration/  e2e/ # e2e: real subprocess and HTTP targets against local fake servers
  examples/                   # one minimal target per access method + a tiny dataset
```

## Layering

- `contracts` imports nothing else from the package. `analysis/` and `report/` read contracts only; they never call
  a target or a judge.
- `runner` is the only module that calls targets; `scorers/judge.py` is the only module that calls a model.
- `server/` reads through `store` and `index`; it never runs a suite. Its only write is an annotation.
- `cli.py` and `pytest_plugin.py` are thin: they parse arguments and call the public API in `__init__.py`.

## Rules

1. **Contracts first.** A new field needs a default (old records stay readable) and a round-trip test that loads a
   record written by the previous `schema_version`. Keep committed sample records under `tests/data/records/`.
2. **The cache key is everything that decides the output** (design §18.5): target config (incl. thinking / effort),
   `versions()` (engine, model snapshot, prompt version), case hash, repeat index. A value that changes the output but
   is missing from `config()` / `versions()` is a bug. Hashing is canonical JSON (§18.6), everywhere.
9. **Design §18 is the tie-breaker**: repeats, statistics, comparability, exit codes, failures, annotations, Viewer
   security, HTTP/subprocess targets, redaction, the CI billing guard, packaging and the index are specified there.
3. **Statistics are tested against hand-computed examples** (McNemar exact p-values, bootstrap with a fixed seed).
   Ranking metrics are cross-checked against ranx in a test, the way lite-rag did.
4. **Failure paths are first-class.** Every target access method has tests for timeout, exception, truncated and
   unparsable output, and a budget stop that leaves a `partial` run.
5. **Retries**: transport errors only, exponential backoff with a capped exponent (design §3 L31), at most 3.
6. **Time budgets in tests are generous** (10 s default); tests about timeouts set their own (L32).
7. **The Viewer API binds loopback only** and refuses to start otherwise; test it like llm-gateway does.
8. Typed throughout (`mypy --strict` on `src/`); async where a target or a judge is called.

## Commands

```
uv sync --locked --all-extras
uv run pytest
uv run pytest -m ollama
uv run ruff check && uv run ruff format --check && uv run mypy
uv run lite-eval --help
```
