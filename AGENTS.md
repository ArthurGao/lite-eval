# lite-eval — Agent Guide

lite-eval is a **general evaluation module for AI applications**: one way to answer "did this change make the
system better or worse, and can we trust that?" for RAG, memory, extraction, judging, grounded writing, agents and
whatever comes next. A system under test is reached as a Python object, an HTTP service or a subprocess; outputs
are generated once and cached, scored by deterministic scorers first and calibrated model judges second, and kept
as immutable run records. A local Viewer shows scores, comparisons and every case. Sister project of
[lite-rag](https://github.com/ArthurGao/lite-rag) and [lite-mem](https://github.com/ArthurGao/lite-mem).

This file is the shared guide for every coding agent. `CLAUDE.md` imports it. `backend/` and `frontend/` each have
their own guide that adds folder-specific rules.

Naming: repo `lite-eval`, Python package `lite_eval`, CLI `lite-eval`, env prefix `LITE_EVAL_`, Viewer port 8720
(Vite dev 8721).

## Repository layout

```
backend/    Python package lite_eval (uv): contracts, runner, scorers, packs, analysis, CLI, pytest plugin, Viewer API
frontend/   Viewer SPA (pnpm): runs, run detail, compare, case detail, miss triage, datasets, trends
docs/       lite-eval-design.md — read before writing code
```

The only contract between backend and frontend is the backend's OpenAPI spec; the frontend generates its client
from it. The frontend build is copied into the Python package, so `lite-eval view` needs no Node.

## Sources of truth (highest first)

1. `docs/lite-eval-design.md` — §2 requirements, **§3 lessons (why every rule below exists)**, §5 contracts,
   §6 runner, §8 analysis, §9 model judges, §10 Viewer, §16 stages and acceptance, **§18 precise specs (the
   tie-breaker when anything is ambiguous)**.
2. Project KB `/Users/arthurgao/Docs/Knowledgebase/projects/lite-eval/` (created in E0) — decisions, findings,
   lessons. Cross-project eval methodology and results: `KB/global/llm-evals/` (read `methodology.md` before
   building a scorer or a judge). Write to the KB with the `kb-maintain` skill.

If a change must contradict the design, update the design in the same change and say why in the commit.

## Current stage

**E4a done (2026-11-14)**: memory pack, HTTP and subprocess targets, `dataset import` (LongMemEval, LoCoMo).
**E4b (2026-11-14)**: streaming import, LongMemEval history in `corpus/`; lite-mem's S11 runs its two-hop eval and
LongMemEval on lite-eval (oracle, S and M measured). **E4c (2026-11-14)**: llm-steward's recall gate runs on
lite-eval (its P8). **E4d (2026-11-14)**: secrets masked by value (`Target.secrets()`), gate rules per config
(`configs:`), gate metrics `errors` / `error_rate` / `scoring_errors`. **E5a (2026-11-18)**: model
judges (Ollama / OpenAI-compatible), calibration runs, extraction / grounded / judging packs, `export kb`
(design §18.24). **E5b (2026-11-18)**: lite-rag S13 calibrates qwen3:8b as its grounded judge (0.982 with
thinking on; a coarse filter, not a gate). **E7 (2026-11-18)**: close-out — whole-repo /simplify and Codex review,
README, releases stay GitHub Release wheels (no PyPI). Next: E6 agent pack, when an agent app needs it; design §16.

## Rules

1. **Contracts are the product.** Case, Output, Trace, Score, RunRecord and Annotation are versioned pydantic models
   (`schema_version`). Only add fields; never change a field's meaning; old records must stay readable (test it).
2. **Generate once, score many.** Calling the system under test and scoring are separate phases; outputs are cached
   by target config + case content. Changing a scorer never calls the target.
3. **Run records are immutable** and carry full provenance: target config (incl. thinking / effort), versions
   (engine, model, channel), dataset hash, scorer versions, git sha, seed, cost. Re-scoring writes a new run.
4. **Failures count.** Errors, timeouts, truncated or unparsable outputs are recorded and scored as failures, and
   their usage is recorded too. Never drop a case from the denominator.
5. **Never claim what the numbers can't show.** Comparisons are paired, with significance; reports say when a
   difference is not significant and how much one case is worth. Warn when different configs score identically.
6. **Deterministic scorers first.** A model judge is used only when no deterministic scorer can decide, and only
   after it is calibrated on a labelled set; its model, channel, prompt version and temperature are recorded.
7. **Every model call names a role** (`judge.<name>`), never a vendor or model; config maps roles to models. No
   fallback chain: a failed call is a scoring error. Prompts live in `backend/src/lite_eval/prompts/*.j2`.
8. **Tests never call a real model or a paid API.** Use the fake target and the fake judge. Real local models (Ollama)
   only under the opt-in marker `-m ollama`. Never call llm-gateway subscription profiles (`gpt-sub`, `claude-sub`)
   or per-token profiles from tests or on your own initiative. CI mode refuses anything not declared `free`, unless
   both `LITE_EVAL_ALLOW_PAID=1` and the suite's `allow_paid_in_ci: true` are set (design §18.13).
9. **lite-eval never depends on a project it evaluates.** Target adapters live in the user's repo. The same
   reason makes the model judges the workspace's one exception to "every LLM call goes through lite-model":
   `judges/client.py` calls Ollama `/api/chat` and OpenAI-compatible endpoints directly, so the instrument stays
   fixed while the systems under test change (design §9, owner-confirmed 2026-11-21).
10. **Honest exit codes.** 0 pass, 1 gate failed, 2 config/input error, 3 run failed. Nothing swallows a failure.
11. **No customer material, real names or keys in datasets or fixtures.** Redact before writing run records when a
    suite asks for it. Secrets only from environment variables; never commit `.env*` except `.env.example`.
12. **The Viewer binds loopback only.** It writes nothing but annotations.
13. **Dependencies**: other projects import lite-eval, so `pyproject.toml` declares ranges for runtime dependencies
    (lower bound = the oldest version the suite passes on, checked by the CI job `lowest`; upper bound = next major);
    `uv.lock` pins exact versions and every run uses `uv sync --locked`. Dev tools stay exact. Verify third-party
    APIs in the installed source or context7, never from memory (decided 2026-11-14, when lite-rag's docling extra
    could not resolve against `typer==0.29.1`).

## Local environment

| What | Where |
| --- | --- |
| Viewer | `127.0.0.1:8720` (`lite-eval view`); Vite dev server `127.0.0.1:8721` |
| Ollama (optional, real local models) | host `127.0.0.1:11434`; start with `brew services run ollama`, stop with `brew services stop ollama` (never `start`/`restart`); keep it stopped by default; check for free with `curl -s http://127.0.0.1:11434/api/tags` |
| llm-gateway (optional, judges in local dev) | `http://127.0.0.1:8787/v1`; only when the owner started it and asked |
| Ports | before using or freeing one: `lsof -ti tcp:<port> -sTCP:LISTEN` + `ps -o command= -p <pid>`; never kill a listener you did not start; register new ports in `KB/global/machine/ports.md` |

## Commands

Backend (from `backend/`):

```
uv sync --locked --all-extras        # a bare `uv sync` drops the extras and looks like a regression
uv run pytest                        # fakes only
uv run pytest -m ollama              # opt-in: real local models
uv run ruff check && uv run ruff format --check && uv run mypy
uv run lite-eval run <suite.yaml> [--config NAME] [--no-cache] [--gate] [--crosscheck] [--ci]
uv run lite-eval compare <baseline run> <candidate run>
uv run lite-eval baseline set <run>
uv run lite-eval rescore <runs/<suite>/<run_id>> [--scorer SPEC]
uv run lite-eval view                # http://127.0.0.1:8720
uv run lite-eval dataset import longmemeval|locomo <official file> <new dataset dir> [--limit N]
```

Frontend (from `frontend/`; Node 22, pnpm 10):

```
pnpm install
pnpm dev                             # Viewer on 127.0.0.1:8721 against a running `lite-eval view`
pnpm generate-client                 # after any backend API change
pnpm typecheck && pnpm lint && pnpm test && pnpm build
pnpm e2e                             # Playwright against the built Viewer
```

## Workflow

1. Work stage by stage (design §16). Each stage ends with every check green; **every new test seen red once**
   (break the line under test, see the key assertion fail, restore). A killed mutation leaves the mutation in the
   source: diff before every check and commit.
2. Definition of done: backend ruff, ruff format, mypy, pytest pass; frontend typecheck, lint, test, build, e2e pass
   when the stage touches it; acceptance items covered by tests; design updated if behaviour changed.
3. Conventional Commits (`feat(runner): …`). All code-facing text (comments, commits, logs) in English.
4. Run tests, read the summary line, and only then commit — never `pytest | tail && git commit` (a pipeline's exit
   status is the last command's). After each push, read CI: `gh run list --limit 3 --json headSha,conclusion`.
5. **Stage close-out — after a stage passes its checks, before its commit and before the next stage starts:**
   1. `/simplify` on the stage's full diff (reuse, simplification, efficiency, altitude); apply the fixes.
   2. Independent read-only Codex review of the same diff (command in `CLAUDE.md`).
   3. Fix every finding that holds up — a bug gets a failing regression test first. A rejected finding is written
      down with the reason (design §16 stage record); never silently dropped.
   4. Re-run every check, then report and commit. Append the stage record to design §16.
6. Git: `git add <paths>` only, never `-A` / `.`; push with a bare `git push`, never naming a remote. Commit and push
   only with the owner's go-ahead.

## Glossary

| Term | Meaning |
| --- | --- |
| Case | One test case: input, expected, tags, provenance |
| Dataset | A versioned, hashed set of cases (+ corpus) |
| Target | One configuration of the system under test: `run(case) → Output` |
| Trace | What the target did on the way: retrieval hits, tool and model calls, decisions with reasons, usage, latency |
| Scorer | `score(case, output) → Score`; deterministic, model judge or human |
| Pack | A task type's defaults: expected shape, scorers, metrics, Viewer display (retrieval, memory, …) |
| Suite | A declaration: dataset × target configs × scorers × metrics × gate × budget |
| Run | One target config of a suite, executed and written as an immutable record |
| Baseline | The run a suite compares and gates against |
| Annotation | A human's triage of one case result (real miss, label wrong, excluded, unclear) |
