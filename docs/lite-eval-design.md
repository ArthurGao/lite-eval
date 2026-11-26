# lite-eval Design Document

> Status: E0–E5b and E7 done (2026-11-13 to 11-18), v0.4.1 released (GitHub Release wheel); next is E6 (agent pack, to be built when there is an agent app). Work follows the stages in §16; when a stage changes behaviour, update this document in the same commit.
>
> **Note for a new session**: this document is the only basis for implementing lite-eval. It was written in an
> llm-steward session and collects everything learned from evals in lite-rag, lite-mem, llm-steward and tenderted.
> Read §0, §2 and §3 first, then start from E0 in §16. The root `AGENTS.md` holds the rules for all agents;
> `backend/` and `frontend/` each add their own.

## 0. How to read this

| What you are doing | Read |
| --- | --- |
| Finding out what to build | §1 Overview, §2 Requirements |
| Finding out why it is designed this way | §3 Lessons (each has a source and numbers) |
| Writing code | The contracts and behaviour in §4–§12, **§18 precise specs (they win when anything is ambiguous)**, §15 repository structure, the acceptance of the current stage in §16 |
| Migrating an existing project's eval | §13, and the original code in each project (paths in "Sources" under §1) |

## 1. Overview

lite-eval is a **general evaluation module for AI applications**: it gives any LLM application (RAG, memory,
extraction, judging, writing, agents, and applications built later) the same eval method. It answers one
question: **did this change make the system better or worse, and can we trust that conclusion?**

It has two parts:

- `backend/`: the Python package `lite_eval`, with contracts, execution, scoring, analysis, the CLI, a pytest plugin, and the read-only API for the Viewer;
- `frontend/`: the Viewer single-page app, for looking at scores, comparing two runs, going through cases one by one and triaging misses.

It belongs to the same family as [lite-rag](https://github.com/ArthurGao/lite-rag) and [lite-mem](https://github.com/ArthurGao/lite-mem):
lite-rag handles document knowledge, lite-mem handles memory about people, and lite-eval measures the quality of both and of every application after them.

### Sources (read the original code here when migrating)

| Source | Content | Home after migration |
| --- | --- | --- |
| `~/Code/AI/lite-ai/lite-rag/src/lite_rag/eval.py` (315 lines), `eval/golden*.jsonl`, `eval/corpus*/`, `eval/configs/*.yaml`, `eval/crosscheck_ranx.py` | Retrieval eval: judge right or wrong by evidence sentence; hit@k, MRR@10, latency; build each index once; warm up before timing; cross-check with ranx | retrieval pack (§7) |
| `~/Code/AI/lite-ai/lite-mem/eval/multihop.py`, `eval/multihop.json`, `eval/dataset.json`, `eval/results/*.json` | Two-hop retrieval; extraction, conflict resolution, sensitive information | memory pack |
| `~/Code/AI/lite-ai/llm-steward/backend/tests/eval/` (`test_recall.py`, `corpus.py`) | pytest gate for joint-retrieval recall@8, threshold 0.92, baseline 0.960 | gate (§8.4) |
| KB `~/Docs/Knowledgebase/global/llm-evals/` (`methodology.md`, `evals/`, `results.jsonl`, `tasks/`) | Methodology, eval cards, cross-project result table | The methodology becomes default behaviour (§3, §8, §9); results can be exported (§13) |
| KB `projects/lite-rag/reference/eval-results.md`, `projects/lite-mem/reference/eval-results.md` | Results and readings of each eval | Lessons (§3) |

### Goals

- **General**: any system under test that can "take an input and return an output" can be tested, whatever language it is written in and however it is deployed (the three ways to connect in §5.3).
- **Stable contracts**: what lasts is the data formats and interfaces, not the implementation. Every record carries `schema_version`; later changes only add fields and never change meaning, so old run records can always be read.
- **Trustworthy conclusions**: the methodology is default behaviour and does not depend on people remembering it (§3).
- **Cheap**: generating outputs and scoring are separate steps, and outputs are cached; deterministic scoring comes first, and a model judge is used only where deterministic scoring cannot do the job.
- **Visible**: a local Viewer (§10).

### Non-goals (v1)

- **No online monitoring or tracing platform**: no production traffic is collected and there are no alerts. When needed, connect a tool such as Phoenix or Langfuse; lite-eval only consumes the datasets they export.
- **No general-capability benchmarks**: MMLU and the like measure the model itself, which is the job of lm-evaluation-harness.
- **No multi-user service**: the Viewer listens on the local machine only (loopback) and has no accounts.
- **No replacement for each project's unit tests**: lite-eval measures quality; correctness is still guaranteed by each project's own tests.

## 2. Requirements (what this module must do)

Each one needs automated acceptance in some stage (§16). The lessons (§3) it rests on are in the last column.

| # | Requirement | Basis |
| --- | --- | --- |
| R1 | The system under test can be a Python object, an HTTP service or a command-line subprocess; lite-eval is only a development dependency and does not enter the production code of the application under test | General |
| R2 | Cases, outputs, scores and run records are all pydantic contracts carrying `schema_version`; fields are only added, and old records can always be read | Long-term use |
| R3 | Two steps: generate first, score after. Outputs are cached by "target config + case content"; when only a scorer changes, the system under test is not called | L7, L23 |
| R4 | Every run leaves an immutable record with full provenance: config, engine and model versions (including vendor, channel, effort, whether thinking is on), dataset hash, scorer version, git sha, seed, cost | L19, L20, L21 |
| R5 | Cases that error, time out or are truncated are still recorded and counted as failures; cost and tokens are recorded on failure too | L6, L18 |
| R6 | Summaries group by tag; comparison with a baseline pairs case by case and gives significance; with few cases it says plainly "the difference is not significant" | L4, L12 |
| R7 | Repeated runs are supported (`repeats`), reporting the mean and the spread; a non-deterministic system under test runs at least 2 times by default | L4, L14 |
| R8 | Every run outputs a miss list; misses can be triaged in the Viewer or the CLI (real miss, label wrong, case excluded, unclear), and triage is stored separately | L1 |
| R9 | Datasets (including labels) have a version and a hash; changing a label makes a new version, and comparison with older runs follows §18.3; a human's triage (Annotation) is append-only and can be corrected, and never changes the dataset | L1, L9 |
| R10 | Deterministic scorers come first; a model judge must first be measured for agreement on a labelled calibration set, and the judge's model, prompt version and temperature are fixed and written into the record | L2, L3 |
| R11 | Gate: the CLI exits non-zero, and a pytest assertion failure prints the regressed cases; the exit code must be truthful | L22 |
| R12 | Budget: cost and call count have limits; in CI mode every call that cannot be confirmed free is refused, unless the environment variable and the Suite both allow it (§18.13) | L7, L24 |
| R13 | Self-checks: warn when configs differ a lot but scores are exactly the same; hint "the dataset may not cover this feature" when no config triggers the feature under test; ranking metrics are cross-checked with a second implementation | L10, L13 |
| R14 | Comparability: identical index configs share one setup; warm up before timing; the latency definition is declared by the system under test; config order does not affect results | L15, L16 |
| R15 | Trace records decisions, not only the final output: retrieval hits, tool calls, model calls, and "why nothing was done" (for example dropped as a duplicate) | L17 |
| R16 | Viewer: run list, run detail, comparison, case detail, miss triage, dataset browser, trends; it can also export a single HTML file | Requirement |
| R17 | Packs by task type: retrieval, memory, extraction, judging, grounded, agent, cost | General |
| R18 | Loaders for external benchmarks: LongMemEval, LoCoMo, and later a BEIR subset; the licence is recorded | L10 |
| R19 | When migrating an existing eval, the numbers before and after migration must be identical item by item | Trust |
| R20 | Datasets hold no customer material, real names or keys; masking rules can be configured before anything is written into a run record, and the Viewer listens on the local machine only | L30 |
| R21 | Results can be exported as rows of the KB `global/llm-evals/results.jsonl` | Cross-project |

## 3. Lessons (why it is designed this way)

Each one follows the form **what happened → conclusion → how lite-eval carries it out**. Sources are in parentheses: "KB methodology" means
`global/llm-evals/methodology.md`; the result pages of lite-rag and lite-mem mean KB `projects/<name>/reference/eval-results.md`.

### 3.1 Eval method

- **L1 Doubt the labels before doubting the model.**
  - What happened:
    - A question-extraction eval first measured recall at 50–65%, with the reference answer being the consensus of several runs of another model. Reading the misses one by one showed that the consensus counted one requirement twice and also took in items the prompt explicitly excluded; against the 14 items really wanted, recall was 100% (KB methodology §1, tenderted).
    - After Codex reviewed the lite-rag baseline, 8 evidence sentences were tightened so that each states the answer directly.
    - One answer the judge model "got wrong" was in fact a wrong label: the answer used "she", but the source text gave no gender (methodology §3).
  - Conclusion: before reporting a gap, read every miss and triage it.
  - How: R8 miss triage; R9 a label change makes a new version.
- **L2 Use constructed labels, but spot-check with real outputs.**
  - What happened:
    - Take one acceptable answer and break it in one known way at a time (invent a fact, add a paragraph, answer a different question, add a forbidden phrase). 13 such cases separated several judge models that real drafts could not tell apart.
    - But the construction inherited the shape of the original answer: the original was short and clean, and that hid the problem. A newly added judge question flagged 45 of 52 normal-length answers (methodology §2).
  - How: the judging pack supports "broken-on-purpose" constructed cases; before a new judge dimension goes live, its flag rate must be checked on a sample of real outputs (R10).
- **L3 A judge model is an instrument: calibrate it, then fix it.**
  - What happened:
    - Scores that different judges gave the same drafts cannot be compared: gpt-5-mini flagged about 11 of 52 as unsupported, while Sonnet flagged 18–20 of 26.
    - A judge can also reward the wrong thing: it marked wrong an answer that correctly flagged "missing evidence", and let through an answer that inferred on its own.
    - Unstable judge conclusions often mean a gap in the scoring rubric (methodology §3).
  - How: R10; if the judge's model, prompt or temperature changes in any way, it counts as a different scorer.
- **L4 Repeat runs and look at the spread; a gap of one or two questions is not a conclusion.**
  - What happened:
    - Three runs of the same config gave coverage of 83%, 83% and 92% (methodology §4).
    - In lite-mem S7 the five configs behaved identically, yet single runs gave 2–4 of 7 on "expired facts", 8–10 of 11 on hit@3, and 7.9–73.9 seconds per write.
    - On lite-rag's 44-question set one question is 0.023, so any gap under about 0.05 counts as a tie; 32 questions are too few to rank close configs.
  - How: R6 significance test; R7 repeated runs. When the difference is not significant the report says so, and must not call it an "improvement".
- **L5 Count the cost of finishing one task, not the per-token price.**
  - What happened:
    - Lowering gpt-5's effort cut writing output by 63%, but unsupported claims rose from 5 to 14 (out of 52 drafts).
    - On a summarisation task, lowering effort fixed a failure instead: the reply at default effort went over the 4000-token limit and returned nothing (methodology §5).
  - How: the cost pack ships with every pack by default, and quality and cost sit in the same table.
- **L6 Hidden costs.**
  - What happened:
    - Reasoning (thinking) tokens are billed as output but do not appear in the reply; they were 88% of gpt-5's writing output.
    - A reply truncated at `max_tokens` is still billed in full; one SDK raised an exception before recording usage, so that spend became invisible (methodology §6).
  - How: R5; `usage` in the Trace is recorded on errors too, with reasoning tokens kept separate.
- **L7 The eval itself needs a budget.**
  - What happened: three full writing evals on gpt-5 used up most of a 10-dollar balance in one day. When only the scoring rubric changed, the stored outputs should have been re-scored (methodology §7).
  - How: R3 two steps and cache; R12 budget.

### 3.2 Data and labels

- **L8 Label by evidence, not by implementation detail.**
  - lite-rag judges right or wrong by "document + evidence sentence from the source", not by chunk id: a chunk id changes when the chunking changes, and an evidence sentence does not.
  - When an evidence sentence is split across two chunks it counts as not answered: that is a real weakness of the chunking, not a problem in the label set.
  - How: §5.1 requires `expected` to refer only to things that hold outside the system under test.
- **L9 The metric must fit the shape of the question.**
  - hit@1 on a multi-hop question is naturally 0: rank 1 is the first link of the chain and the answer sits at ranks 2–3, so look at hit@5 (lite-rag S11).
  - lite-mem's two-hop questions are judged by "every hop's keyword group is in the top k".
  - How: the pack decides the default metrics; a case can use tags to declare which k it should be judged at.
- **L10 A synthetic dataset may not test the feature at all.**
  - What happened:
    - lite-mem S7 swept the merge threshold over 0.80, 0.85, 0.90 and 0.95, and all four configs merged nothing. The reason is that conflict resolution had already dropped the duplicates at write time, so the synthetic set left nothing to merge, and the threshold could only be set from the similarity table in the end.
    - Every system (mem0 included) stored the bank card PIN `482915` as a memory, which shows the dataset needs cases for "this must not happen".
  - How:
    - R13: when all configs give the same result, hint that the dataset may not cover the feature;
    - `expected` supports negative expectations (`must_not`);
    - synthetic data is marked as such in the report;
    - external benchmarks (LongMemEval, LoCoMo) check whether conclusions generalise (R18).
- **L11 A number from a small dataset says one thing: a big gap is real, a small gap is noise.**
  - On the 19 checks of lite-mem S5, mem0 kept 7 of the 7 expired facts and lite-mem kept 1: a big gap, and a conclusion can be drawn.
  - A gap of one or two items does not allow a conclusion.
  - How: the report marks each conclusion with its strength of evidence, for example `measured, one run, small set`, using the same wording as the KB.
- **L12 The number of cases decides how small a gap can be told apart.**
  - How: the report's table header states "1 case = how many points", so the reader can judge for themselves.

### 3.3 The system under test and comparability

- **L13 Bugs in the system under test disguise themselves as eval results.**
  - What happened: MiniLM's tokenizer file carries a 128-token truncation, and fixed-size chunking silently dropped the text after it. The result was a hit@1 of 0.25 in every retrieval mode (BM25 included) (lite-rag matrix of 2026-11-11).
  - Conclusion: configs that differ a lot but get exactly the same score are a red alarm, not a conclusion.
  - How: the "exactly the same" warning in R13; ranking metrics are cross-checked with ranx (lite-rag checked all six configs with ranx 0.3.21).
- **L14 A non-deterministic system changes on every run.**
  - Graph extraction uses a local model, and one question changed result between two runs; qwen3:8b sometimes returns 500 or times out.
  - How: R7; cases that error count as failures and are also counted separately.
- **L15 One component's effect depends on the other components.**
  - rerank cancelled the gain from graph retrieval: the cross-encoder compares only the question with a single chunk, and prefers sibling chunks that look alike.
  - Once rerank is used, which embedding is used makes almost no difference.
  - How: a Suite supports a config matrix, and identical index configs share one setup (R14).
- **L16 State the latency definition clearly and leave out cold start.**
  - lite-rag's first config in the first run showed 40 ms, which was really an uncached query vector, not tokenizer loading.
  - Changed to: compute the query vector before timing, then run one untimed warm-up search.
  - How: the Target declares the latency definition; the runner warms up after setup (R14).
- **L17 "Decisions not to act" must leave a trace too.**
  - In lite-mem, a fact judged a duplicate (NOOP) and dropped writes no event, so the history does not show why it disappeared.
  - The problem "Shanghai was treated as a duplicate of Beijing" was found only through the reasons recorded on other events.
  - How: a Trace span can record a decision and its reason (R15), and the Viewer's case detail shows it.

### 3.4 Models, channels and the local machine

- **L18 Local models are slow and unstable.**
  - qwen3:8b takes 7.9–73.9 seconds per write, and sometimes returns 500 or times out.
  - When llm-steward synced with a local model, a single call went over the worker's 30-second limit and needed retries to finish.
  - Once qwen3:8b extracted "moved house last week" but lost the city.
  - How: every case has a timeout setting, and a timeout counts as a failure and is recorded as usual; default concurrency is low (4).
- **L19 Thinking mode changes quality and speed, so it must be recorded as config.**
  - With reasoning off, qwen3:8b judged "lives in Shanghai" a duplicate of "lives in Beijing" and dropped it; with reasoning on it judged correctly, but about 5 times slower (47 seconds per conversation against 10 seconds).
  - How: the Target's `config` must contain the thinking and effort settings (R4).
- **L20 Subscription channels and the API give different results.**
  - How: the run record states the channel used (for example, the llm-gateway profiles `claude-sub` and `claude-api` are two different things). Never ship on results from a subscription channel alone (KB `global/llm-gateway/README.md`).
- **L21 To be reproducible, record exact versions.**
  - Every result page states the engine versions (mem0 2.2.1, ranx 0.3.21, bge-m3:567m, pydantic-ai-slim 2.61.0), and a change means re-measuring.
  - How: R4.
- **L24 Never call subscription or paid models in tests.**
  - Unit and integration tests use fake models; real local models run only under an opt-in marker.
  - llm-gateway's subscription profiles (`gpt-sub`, `claude-sub`) and per-token profiles are called only when the owner asks.
  - How: R12 CI mode; lite-eval's own tests use only a fake Target and a fake judge.

### 3.5 Engineering and process

- **L22 A pipeline's exit code lies.**
  - `pytest | rg | tail && git commit` committed twice while tests were failing (lite-mem), because a pipeline's exit code is that of the last command (`tail`).
  - How:
    - the gate's exit code must be truthful (R11), and lite-eval's CLI must not swallow a failure into 0;
    - in our own workflow, run the tests and read the summary line first, then commit in a separate step.
- **L23 A new test must have been seen failing once.**
  - After writing the test, break the line under test on purpose, confirm the key assertion fails, then restore.
  - An interrupted break-the-line script leaves its change in the source, so look at the diff before committing.
- **L25 Nobody notices a red CI.**
  - llm-steward's CI was red from P5 to P7: a local `.env` hid one failing test.
  - How: read the CI result after every push (`gh run list`); tests do not depend on a local `.env`.
- **L26 A bare `uv sync` drops extras**, and the failure looks like a regression. Always use `uv sync --locked --all-extras` (CI too).
- **L27 Tests on a shared database must not run two at once.** Each project uses its own test schema. lite-eval's SQLite index uses a temporary directory in tests.
- **L28 A pydantic-settings field alias makes a prefixed environment variable stop working.**
  - In lite-mem, a `LITEMEM_SCHEMA` set by a test had no effect, and the migration ran on the application schema.
  - How: settings fields use no aliases, and a test confirms the environment variable really takes effect.
- **L29 A second implementation finds the problem the first one hid.**
  - Only after lite-mem added SQLite storage was it found that uuid7 values generated in the same millisecond sort randomly.
  - How: the run directory (files) and the SQLite index run the same read tests; the order of `run_id` must be stable (§12).
- **L30 Run records contain prompts and outputs, which may hold sensitive content.**
  - lite-mem's masking took four review rounds to complete (`sslpassword`, passwords containing `&`, prefixes such as `PGPASSWORD`).
  - The KB says not to record customer material, personal names or keys.
  - How: R20, reusing lite-mem's masking approach (a whole connection string is parsed as libpq does, free text is handled by rules), with a test for every form.
- **L31 Cap the exponent of an exponential backoff.**
  - llm-steward's polling delay made `timedelta` overflow at about the 47th attempt; the fix limits the number of doublings to `ceil(log2(cap / start))`.
  - How: the runner's retries are implemented this way.
- **L32 Give tests a generous time budget.** On a shared CI machine, one fake search took over 800 ms. The default is 10 seconds, and tests that exist to test timeouts set their own.
- **L33 Ports and processes.**
  - Before killing a process or freeing a port, check who owns it (`lsof -ti tcp:<port> -sTCP:LISTEN` + `ps -o command= -p <pid>`).
  - Register a new port in KB `global/machine/ports.md`.
  - Start Ollama with `brew services run ollama` and stop it with `brew services stop ollama`, not `start` or `restart`; it stays stopped by default.
- **L34 Never write third-party APIs from memory.** For libraries such as ranx, FastAPI, pydantic and Inspect AI, check context7 or the installed source first, and write the verified version in a comment.

## 4. Core concepts and data flow

```
Suite (declaration of one eval: dataset + system under test and its configs + scorers + metrics + gate + budget)
  │
  ▼
Dataset ──▶ Target ──▶ Output + Trace ──▶ Scorer ──▶ Run Record ──▶ Analysis
 (cases)    (system under test)   (output + intermediate process)    (scoring)      (immutable record)    (summary / comparison / gate / report / Viewer)
                │                              ▲
                └──── output cache (by config + case) ───┘   scoring rubric changed: re-run scoring only
```

| Concept | Meaning |
| --- | --- |
| Case | One case: input, expectation, tags, source |
| Dataset | A set of cases, with a name, a version and a content hash |
| Target | One configuration of the system under test: `run(case) → Output (with Trace)` |
| Trace | The intermediate process of the system under test: retrieval hits, tool calls, model calls, decisions and reasons, tokens, time, cost |
| Scorer | A scorer: `score(case, output) → Score` |
| Pack | The default bundle for one task type: the format of `expected` + default scorers + summary metrics + how it is shown in the Viewer |
| Suite | A declaration (YAML or Python) |
| Run | The result of one Target config of a Suite, written as an immutable run record |
| Baseline | A run designated as the reference; the gate and comparison both go by it |
| Annotation | A human's triage and note on one case result, stored separately |

## 5. Contracts

All contracts are pydantic models in `backend/src/lite_eval/contracts.py`, serialised as JSON or JSONL, and every record carries
`schema_version` (starting at 1). The frontend reads these contracts only through the client generated from OpenAPI.

### 5.1 Case

| Field | Type | Description |
| --- | --- | --- |
| `id` | str | Unique and stable within the dataset: changing the content does not change the id |
| `input` | JSON | Structure is set by the pack. Retrieval: `{"query": …}`; memory: `{"history": […], "query": …}`; agent: `{"task": …}` |
| `expected` | JSON \| null | Structure is set by the pack. Retrieval: `{"doc": …, "evidence": …}`; two-hop: `{"hops": [[…], […]]}`; negative: `{"must_not": […]}` |
| `tags` | dict[str, str] | For grouped statistics: `kind`, `lang`, `difficulty`, `split`, `k` (which k this question should be judged at) |
| `provenance` | `{source, synthetic, author, created, license}` | Source |

`expected` may refer only to things that also hold outside the system under test, such as an evidence sentence from the source, a document name, or fact keywords; it must not refer to implementation details such as chunk ids (L8).

### 5.2 Dataset

- A directory: `dataset.yaml` (name, version, description, pack, corpus location, licence, `synthetic`) + `cases.jsonl` + an optional `corpus/`.
- **Content hash**: cases are sorted by id and their JSON is normalised before computing sha256, and the corpus files are included. The hash is written into every run record.
- **Loaders**: a local directory; the external benchmarks LongMemEval and LoCoMo (E4), and later a BEIR subset. A loader converts to Case and records the original source and licence.

### 5.3 Target

```python
class Target(Protocol):
    name: str
    def config(self) -> dict[str, Any]: ...            # everything that decides the output: model, thinking/effort, parameters, index config; goes into the cache key
    def versions(self) -> dict[str, str]: ...          # versions that affect the output: engine, model snapshot, prompt; goes into the cache key (§18.5)
    def info(self) -> dict[str, str]: ...              # record only: git sha, host, calling channel; not in the cache key
    def billing(self) -> Literal["free", "subscription", "per_token", "unknown"]: ...   # CI guard (§18.13)
    def setup_identity(self) -> dict[str, Any]: ...    # equal values share one setup (for example the index config; §18.18)
    async def setup(self, dataset: Dataset) -> None: ...   # build the index, load data; not timed
    # optional: async def share_setup_from(self, leader: "Target") -> None   # take over the setup another instance built (§18.18)
    async def run(self, case: Case) -> Output: ...
    async def teardown(self) -> None: ...
```

Three ways to connect:

| Way | Fits | Description |
| --- | --- | --- |
| Python | Python applications | Implement the protocol above; lite-eval is only a development dependency |
| HTTP | Any application with an API (TS, Go, …) | The Suite gives the URL, a request template (Jinja2, the variable is `case.input`), and JSONPaths to take `output` and `trace` from the response |
| Subprocess | Command-line tools, other languages | For each case, write one JSON line `{"case": …, "repeat": n}` to stdin and read one line `{"case_id": …, "repeat": n, "output": …, "trace": …}` from stdout; the process stays alive, runs cases one at a time, and serves all the cases (§18.11) |

### 5.4 Output and Trace

```
Output: {schema_version, case_id, repeat: int, value: JSON | null, trace: Trace, error: {kind, message} | null}
Trace:  {spans: [Span], usage: {input_tokens, output_tokens, reasoning_tokens, cost_usd}, latency_ms, latency_scope}
Span:   {kind: "retrieve" | "rerank" | "llm" | "tool" | "memory.write" | "memory.read" | "decision" | str,
         name, start_ms, duration_ms, input, output, attrs}
```

- `repeat` starts at 0 (§18.1).
- `error.kind` takes the values `timeout`, `exception`, `truncated`, `invalid_output`, `http_status`: all count as failures and are counted by category in the report (R5).
  On an error, `trace.usage` and `latency_ms` are still filled in with the parts that are known (§18.8).
- `kind="decision"` records the system under test's decision and its reason, for example `{"op": "NOOP", "reason": "duplicate of …"}` (L17).
- `latency_scope` is the latency definition declared by the Target, for example `"retrieval+rerank, query vectors precomputed"` (L16).

### 5.5 Score and Scorer

```
Score: {schema_version, case_id, repeat, scorer, scorer_version, value: float, passed: bool | null,
        reason: str, evidence: JSON, judge: JudgeRecord | null, error: {kind, message} | null}
JudgeRecord: {role, model, channel, prompt_version, temperature, raw}
```

- `Score.value` is in [0, 1], larger is better; the result of a combined scorer is in this range too. Latency, cost and tokens are not Scores; they are summary metrics computed from the Trace (smaller is better), and the gate uses `max` on them (§18.19).

```python
class Scorer(Protocol):
    name: str
    version: str                       # bump the version whenever the scoring logic changes
    def score(self, case: Case, output: Output) -> Score: ...
```

| Class | Examples | Description |
| --- | --- | --- |
| Deterministic | Evidence-sentence containment (whitespace normalised, case per pack setting), keyword groups, exact match, set recall and precision, ranking metrics (our own implementation, cross-checked with ranx), JSON schema, regex, `must_not` | First choice |
| Model judge | Faithfulness, whether the answer is off-topic, rule compliance | Usable only after calibration (§9) |
| Human | Labelling case by case in the Viewer | Stored as Annotation, used for calibration sets and spot checks |

Scorers can be combined: `all_of`, `any_of`, weighted average. The result of each scorer is kept separately.

### 5.6 Run Record

One directory per run (§12):

- `run.json`:
  - identity: `schema_version`, `run_id`, `suite`, `target.name`;
  - system under test: `target.config`, `target.versions`;
  - dataset: `dataset{name, version, hash, synthetic}`;
  - scorers: `scorers[{name, version}]`;
  - run parameters: `repeats`, `seed`, `redact` (the names of the masking rules in effect and custom regexes, reused on export, §18.12);
  - run environment: `started_at`, `finished_at`, `host`, `lite_eval_version`, `git_sha`, `git_dirty`;
  - result: `status` (`complete`, `partial`, `failed`), `summary`, `cost`, `errors`, `warnings`;
  - origin: `rescored_from`.
- `outputs.jsonl`: one Output line per case (per repeat).
- `scores.jsonl`: one Score line per case and per scorer.

A run record is **never changed** once written. Re-scoring produces a new run, and `rescored_from` points to the original run.

## 6. Execution (runner)

- **Two steps**:
  - Generate first: call the Target and write the result into the cache. For the cache key see §18.5 (config, versions that affect the output, case hash, repeat index).
  - Then score. When the scorer, a threshold or the judge model changes, only scoring is re-run (`lite-eval rescore`).
  - `--no-cache` forces regeneration.
- **Repeats**: `repeats: n`. The report gives the mean, minimum and maximum; when a Target declares itself non-deterministic, the default is `n = 2`.
- **Concurrency**: cases run in parallel (`concurrency`, default 4); a subprocess Target is the exception and runs strictly one at a time inside its process (§18.11). Each case has a timeout (`timeout_s`, default 120), and a timeout counts as `error.kind=timeout`.
- **Comparability**:
  - configs with the same `setup_identity` share one setup (lifecycle in §18.18);
  - after setup, one untimed warm-up runs (§18.18);
  - the order in which configs run does not affect results (verified in tests by shuffling the order).
- **Retry**: only transport-layer errors are retried, with exponential backoff and a capped exponent (L31), at most 3 times. Business errors returned by the system under test are not retried.
- **Budget**: `budget.max_cost_usd`, `budget.max_calls`. At the limit it stops, the completed part is recorded as usual, and `status=partial`.
- **CI mode** (`--ci` or `CI=true`):
  - for the billing guard see §18.13: everything that cannot be confirmed free is refused;
  - the local `.env` is not read.
- **Self-checks** (R13): after a run, check the following and write them into `warnings`:
  - several configs have exactly the same per-case scores;
  - all cases fail;
  - some tag group has no cases;
  - the dataset is synthetic.

## 7. Pack (by task type)

| Pack | `expected` | Default scorers and metrics | First user |
| --- | --- | --- | --- |
| `retrieval` | `{doc, evidence}` or `{relevant: [ids]}` | Evidence hit; hit@k, MRR@10, nDCG@k, recall@k (cross-checked with ranx); latency P50/P95 | lite-rag, llm-steward |
| `memory` | Multi-hop: `{hops: [[kw…]…]}`; evidence: `{relevant: [session or dia_id]}`; `must_not` | Hit per hop (`hop<n>`), all hops hit (`all_hops`); evidence recall (`recall@k`); forbidden content (`must_not`). Deferred: extraction (`{facts}`), update (`{superseded, kept}`) and so on, until a project needs to test them; refusal goes to E5's model judge (§18.23) | lite-mem; LongMemEval, LoCoMo |
| `extraction` | `{items: […]}` | `item_recall` (recall first, evidence lists the missed items), `item_duplicates` (share of duplicates); no precision gate (§18.24) | tenderted kind |
| `judging` | `{label}` | `label_agreement` (agreement rate), grouped by the `construction` tag; the confusion matrix is computed from the scores' evidence; construction tool `judges/construct.py` (§18.24) | Calibrating a judge model (§9) |
| `grounded` | `{must: […], must_not: […]}` | `contains` (must, looks at `answer`), `must_not`, judge `judge.grounded` (whether the answer has claims the sources do not support); rules for relevance to the question will be added later as needed | RAG writing |
| `agent` | `{goal, checks: […]}` | Task completion, correct tool calls, step count, cost | Future agent applications (E6) |
| `cost` | — | Tokens per case (reasoning included), cost, latency; every pack carries it | All |

## 8. Analysis

### 8.1 Summary

- Every metric gives an overall value and is grouped by `tags`.
- The report's table header states "1 case = x points" (L12), and cases that error or are partial are counted separately.
- Latency percentiles are uniform everywhere: `p50` = `statistics.median`, `p95` = nearest-rank (after sorting, take the `ceil(0.95·n)`-th value), with no interpolation; this matches lite-rag's definition in §18.16.
- Every conclusion carries its strength of evidence (L11).

### 8.2 Comparison

`compare(baseline, candidate)`:

- **Compare only what is comparable**: the two are compared directly only when the dataset hash and scorer versions are both the same; otherwise a warning is given and only the cases present on both sides are compared.
- **Pair case by case**: newly failed, newly fixed, failed on both sides, passed on both sides. **Regressions come first**.
- **Significance**:
  - binary metrics use the exact McNemar test;
  - continuous metrics use a paired bootstrap (10,000 resamples, fixed seed) and give a 95% interval.
  - When not significant, it says so directly: "difference not significant (n=20, 1 discordant case)".
- **Repeated runs**: pair using each case's mean; when the spread is larger than the difference, a warning is given.

### 8.3 Miss triage

- Every run outputs a miss list (the cases that did not pass).
- In the Viewer or with `lite-eval triage`, triage them one by one: `real_miss`, `label_wrong`, `excluded`, `unclear`, with an optional note.
- Triage is stored as Annotation, attached to `(dataset name, case id, dataset hash)`, and later runs show it automatically.
- Cases triaged as `label_wrong` go into a "labels to fix" list; once fixed, a new dataset version is made, and they cannot be passed over by "ignoring" them.

### 8.4 Gate

- **Written in the Suite**:
  - absolute threshold: `gate: [{metric: hit@5, min: 0.92, baseline_note: "0.960 on 2026-11-09"}]`;
  - relative threshold: `{compare_to: baseline, max_drop: 0.02, require_significant: true}`.
  - The exact semantics (direction, `max`, repeated runs, `require_significant`) are in §18.19.
- **How to set a threshold**: a little below the measured baseline, leaving about one case of margin; write the baseline value and date beside it (from llm-steward's recall gate).
- **Entry points**:
  - CLI: `lite-eval run --gate`, which exits with code 1 when the gate does not pass and code 2 on a config error;
  - pytest: `lite_eval.assert_gate(run)`, whose failure message lists the regressed cases;
  - Python API.

## 9. Model judges

- **How it is called**: through a role (`judge.<name>`), with the role-to-model mapping in config.
  - Local development goes through llm-gateway (`http://127.0.0.1:8787/v1`) or Ollama;
  - no vendor or model name is hard-coded in code;
  - no fallback chain: a failed call counts as a scoring error.
- **Not through lite-model (an explicit exception, confirmed by the owner 2026-11-21)**: the workspace convention is "every LLM call goes through a lite-model role",
  and the judge is the only exception; the calling code lives only in `judges/client.py` (§18.24). Reasons:
  - The judge is a measuring instrument and must stay fixed and isolated from the stack under test: every project being evaluated depends on lite-model, and if the judge went through it too, a
    change in lite-model would change the system under test and the instrument at the same time (the same reason as rule 9).
  - lite-model's `ollama` backend goes through the OpenAI-compatible `/v1`; the judge needs the boolean `think` of Ollama's native `/api/chat`
    and the `thinking` text in the reply.
  - When the output is invalid, `JudgeRecord.raw` must keep the raw reply (§18.24); lite-model's `BadStructuredReply` carries no upstream text, by its own rules.
  - The judge identity (§18.24) is decided by `JudgeSpec`, and the calibration runs lite-rag already has match on these keys.
  The same principles still apply: configured by role, no fallback, cost and channel recorded in `JudgeRecord`, and paid calls governed by the switch in §18.13.
  This corresponds to lite-model design §1 Non-goals and §15 (closed).
- **Calibrate before use**:
  - Every judge dimension needs a calibration set with human labels, which can get certain labels by the construction method of L2.
  - Calibration is one run of the `judging` pack; only when the agreement rate reaches the threshold in the Suite may that judge be referenced in a formal Suite.
  - Before a new dimension goes live, its flag rate must also be checked on a sample of real outputs (L2).
- **Fixed**: model, channel, prompt version and temperature are all written into `JudgeRecord`; if any of them changes, the scorer version changes.
- **Structured output**: the judge returns `{verdict, score, reason, evidence}`, validated by pydantic; a parse failure is recorded as a scoring error and does not count as a pass.
- **Prompts** live in `backend/src/lite_eval/prompts/*.j2` and are not written as inline strings.
- **Tests**: use a fake judge; real models run only under the opt-in marker (`-m ollama`).
- **Landing (E5a)**: the exact specs for calls, calibration checks, async scoring, cost and KB export are in §18.24. "Formal Suite" = a Suite with a gate: an uncalibrated judge can still run (to explore and to see the flag rate on real outputs), but the run carries a `judge_uncalibrated` warning, and gate rules that read its metrics are rejected (decided by the owner 2026-11-18: other rules in the same Suite are unaffected).

## 10. Viewer (frontend)

`lite-eval view` starts on the local machine: FastAPI serves a read-only API (plus Annotation writes) and also hosts the built single-page app.
It defaults to `127.0.0.1:8720`, listens only on loopback, and refuses to start on a non-loopback address.

### 10.1 Pages

| Page | Content |
| --- | --- |
| **Runs** | All runs: suite, target config, dataset version, main metrics, cost, time, status; filter by suite, pack, tag; baseline, synthetic datasets and runs with warnings are marked |
| **Run detail** | Summary and grouped metrics (the header says "1 case = x points"); cost and latency distribution; the full config and version record; error categories; self-check warnings |
| **Compare** | Pick two runs: metric differences, significance and interval; per-case changes, regressions first; a warning when the dataset or scorers do not match |
| **Case detail** | Input, expected and output side by side; Trace timeline (retrieval hits, tool calls, model calls, decisions and reasons, time, tokens); each scorer's score, reason and evidence; the judge's raw reply; the same case's results in other runs |
| **Miss triage** | Work through misses one by one, triage them and add notes; the "labels to fix" list |
| **Datasets** | Browse cases, source, licence, version and hash; check whether labels are right, and add a "label wrong" Annotation (the dataset file is not changed, §18.9) |
| **Trends** | How a suite's metric changes over time or git commits, marking where the baseline switched and where the dataset changed version |

### 10.2 Technology

- **Frontend**: React + Vite + TypeScript, TanStack Router and Query, shadcn/ui + Tailwind, Vitest, Playwright, in line with llm-steward's
  Admin (see `~/Code/AI/lite-ai/llm-steward/frontend/`). The client is generated from the backend's OpenAPI (`@hey-api/openapi-ts`).
- **Bundling**: the built static files are copied into the Python package (`lite_eval/server/static/`). After `pip install lite-eval` you run `lite-eval view` directly,
  and the user's machine needs no Node.
- **Data**:
  - files (the run directories) are the source of truth;
  - at startup the run directory is scanned and a SQLite index is built (in `.lite-eval/index.sqlite`), used only for filtering and trend queries, and it can be deleted and rebuilt at any time;
  - when the run directory has new content, the index is updated incrementally.
- **The only write operation is Annotation**: appended to `annotations.jsonl`; run records and datasets are not changed.
- **Static export**: `lite-eval report --html` produces a single self-contained HTML file (data embedded, no connection to a server), which can be sent to others or published as an Artifact.
- **Design direction**: a dense data tool, see the KB's `design-direction` skill; pages are mainly tables and side-by-side comparison.

## 11. Interfaces

### 11.1 Suite (YAML)

```yaml
schema_version: 1
name: lite-rag-retrieval
pack: retrieval
dataset: datasets/lite-rag-golden          # or {loader: longmemeval, path: …, variant: s}
targets:
  - python: lite_rag_eval_target:Target    # or http: {...} / command: [...]
    configs:                               # one Target, several configs → several runs
      - {name: hybrid, mode: hybrid}
      - {name: hybrid-rerank, mode: hybrid, rerank: true}
scorers: [evidence_hit]                    # if omitted, the pack's default scorers are used
metrics: [hit@1, hit@3, hit@5, mrr@10, latency_p50, latency_p95]
repeats: 1
concurrency: 4
timeout_s: 120
gate:
  - {metric: hit@5, min: 0.90, baseline_note: "0.95 on 2026-11-08"}
budget: {max_cost_usd: 0}
redact: [connection_strings, api_keys]
```

### 11.2 CLI (`lite-eval`)

```
lite-eval run <suite.yaml> [--config NAME] [--no-cache] [--gate] [--crosscheck] [--ci]
lite-eval rescore <run> [--scorer …]          # re-run scoring only
lite-eval compare <baseline> <candidate>
lite-eval triage <run>                        # triage misses one by one in the terminal
lite-eval baseline set <run>                  # the run record holds the suite name
lite-eval view [--root DIR] [--host 127.0.0.1] [--port 8720] [--check]
lite-eval report --html <run | baseline..candidate> -o out.html [--redact RULE]
lite-eval export kb <run>                     # rows for the KB results.jsonl (§13)
lite-eval datasets list | show <name> | hash <path>
```

Exit codes: 0 pass; 1 gate failed; 2 config or input error; 3 run failed. The exact definition and priority are in §18.4.

### 11.3 Python and pytest

```python
from lite_eval import run_suite, assert_gate
run = run_suite("evals/suites/retrieval.yaml", config="hybrid")
assert_gate(run)                     # call it inside pytest; on failure it prints the regressed cases
```

## 12. Storage and directories

**By default it lives in the user's repository**, so run records travel with the code and can be traced by commit:

```
<user repository>/evals/
  suites/*.yaml
  datasets/<name>/{dataset.yaml, cases.jsonl, corpus/…}
  runs/<suite>/<run_id>/{run.json, outputs.jsonl, scores.jsonl}
  baselines.yaml              # suite → run_id used as the baseline
  baselines.log.jsonl         # one line appended on every `baseline set` (since E3); the trends page marks switches from it
  annotations.jsonl
  .lite-eval/                 # gitignored
    cache/                    # output cache
    index.sqlite              # the Viewer's index, can be rebuilt
```

- The format and ordering of `run_id` are in §18.7.
- The run that the baseline points to is committed to the repository, and the rest is gitignored (§17).

## 13. Relationship to existing projects and the KB

- **Migration**: whenever a project is brought in, the numbers before and after migration must be identical item by item (same config, same data). After migration, the project's old eval scripts are deleted and replaced by calls to lite-eval:
  - lite-rag: E2 (migrated, 2026-11-14, lite-rag S12: `literag eval` is a thin wrapper over lite-eval);
  - lite-mem and llm-steward: E4.
- **Adapters for the system under test live in the user's repository** (for example `LiteRagTarget` in lite-rag's `src/lite_rag/eval.py`); lite-eval depends on no project under test.
- **KB**:
  - `lite-eval export kb <run>` produces rows in the `global/llm-evals/results.jsonl` format (fields follow the schema in the KB README), and then the eval card is written under the KB's rules;
  - conclusions on model choice are still recorded in the KB, and lite-eval only computes the numbers.
- **lite-eval's own project KB** is `projects/lite-eval/`, created in E0, and holds decisions, lessons and pitfalls. Write to it with the `kb-maintain` skill.

## 14. Open-source references and trade-offs

| Project | What we take | What we do not take |
| --- | --- | --- |
| Inspect AI (UK AISI, MIT) | The Dataset → Solver → Scorer layering; logs plus a viewer; the agent sandbox | The core does not depend on it: it centres on "calling a model", and fitting our deterministic retrieval and memory evals into it does not pay off. When E6 builds the agent pack it serves as an optional execution backend: convert Case and Scorer into an Inspect Task, then convert its logs back into run records |
| promptfoo (MIT) | Declarative config, comparison view, CI usage | A Node implementation, aimed at the prompt layer |
| DeepEval | pytest-style gates | Most of its metrics rely on an LLM judge |
| Ragas (Apache-2.0) | The definitions of generation-side RAG metrics: faithfulness, context precision and recall | Maintenance status to be verified (reportedly no commits since 2026-02), so not used as a dependency |
| ranx | An optional extra `[ranking]`, used only for cross-checking (`--crosscheck`): ranking metrics are computed by `scorers/ranking.py` itself, with definitions word for word the same as lite-rag's; ranx (tested against TREC Eval) computes them again for comparison | Correction (E4b): this originally said "direct dependency", which did not match the code |
| LongMemEval (ICLR 2025), LoCoMo, BEIR | External benchmarks, built as dataset loaders | Cannot be compared directly with others' self-reported scores: the judge model and token budget differ |
| Phoenix, Langfuse | Later, datasets can be taken from the traces they export | We do not build a tracing platform |

## 15. Repository structure

```
lite-eval/
  AGENTS.md  CLAUDE.md  README.md  LICENSE
  docs/lite-eval-design.md
  backend/                         # Python package lite_eval (uv)
    AGENTS.md  CLAUDE.md
    pyproject.toml
    src/lite_eval/
      contracts.py                 # Case, Dataset, Output, Trace, Span, Score, JudgeRecord, RunRecord, Annotation
      config.py                    # pydantic-settings, prefix LITE_EVAL_, fields use no aliases
      datasets/                    # local directory loading, hashing; loaders/ (longmemeval, locomo)
      targets/                     # python.py, http.py, command.py, fake.py (for tests)
      scorers/                     # deterministic.py, ranking.py (ranx only cross-checks), judge.py, judging.py, compose.py
      judges/                      # spec.py, rubric.py, client.py (the only code that calls a model), fake.py, target.py, construct.py
      packs/                       # retrieval, memory, extraction, judging, grounded, agent, cost
      runner.py                    # two steps, cache, concurrency, timeout, retry, budget, setup sharing, warm-up, self-checks
      store.py                     # run directory read/write, baseline, annotation
      index.py                     # the Viewer's SQLite index
      analysis/                    # summary.py, compare.py (McNemar, bootstrap), gate.py
      report/                      # terminal.py, html.py, kb.py
      redact.py
      server/                      # FastAPI read-only API + static/ (frontend build output, copied in at build time)
      prompts/                     # judge prompts *.j2
      cli.py                       # Typer
      pytest_plugin.py
    tests/{unit,integration,e2e}/  # e2e: real subprocess and HTTP Targets (fake services started locally)
    examples/                      # one minimal example per way of connecting + example datasets
  frontend/                        # Viewer (pnpm)
    AGENTS.md  CLAUDE.md
    package.json
    src/{client/ (generated), routes/, components/, lib/}
    tests/ (Playwright)
```

## 16. Stages and acceptance

At the end of each stage, all of these checks must pass:

- backend: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest`;
- for stages with a frontend, also: `pnpm typecheck`, `pnpm lint`, `pnpm test`, `pnpm build`, `pnpm e2e`;
- every new test has been seen failing at least once (L23).

Then do the stage close-out (see `CLAUDE.md`):

1. Run `/simplify` on the whole stage's diff and apply the changes;
2. Have Codex review the same diff, read-only;
3. Fix the findings that hold up (for a bug, write a failing test first), and write down the reason for those that do not;
4. Re-run all checks and report the results; with the owner's agreement, commit and push, then read the CI result;
5. Add an entry under "Stage records" below.

Do not start the next stage before the close-out is done.

**The finish line of v1 is the end of E3**: E0–E3 deliver the core features (contracts, two-step runner, deterministic scoring, comparison and gate, the lite-rag migration) and the Viewer.
The Viewer stays in v1 because miss triage (L1, the first lesson) has to be done case by case with it; the trends page and HTML export are built on the index and API that E3 already has,
add little, and are not split out. E4 and later are extensions after v1.

| Stage | Content | Acceptance |
| --- | --- | --- |
| E0 Environment | Repository skeleton (backend uv project, frontend pnpm project, CI); check and lock the versions and APIs of pydantic, pydantic-settings, typer, ranx, scipy, fastapi, uvicorn, jinja2, httpx2 using context7 or the installed source (use httpx2, not httpx: the openai SDK and Starlette's TestClient both use it, see the E0 stage record); register ports 8720 (Viewer) and 8721 (Vite dev) in KB `global/machine/ports.md`; create the project KB `projects/lite-eval/` | `lite-eval --help` works; CI green; KB created and passes validation |
| E1 Contracts and execution | `contracts`; local datasets and canonical hash (§18.6); Python Target and fake Target; deterministic scorers; the two-step runner (cache, repeats, concurrency, timeout, retry, budget, self-checks, masking); run directory (atomic writes); `run`, `rescore`; terminal report; cost pack | Running the same Suite twice, the second run hits the cache for everything and gives the same result; the cache is invalidated when `versions()` changes (§18.5); after a scorer change `rescore` does not call the Target; contracts round-trip, and old-version records are readable; errors, timeouts, truncation and scorer errors are all recorded and counted in the denominator (§18.8); after the budget is reached `status=partial` and the exit code follows §18.4; results are the same after shuffling config order; CI mode refuses to run a Target or judge that has not declared its billing (§18.13); the two self-checks `identical_scores` and `feature_not_exercised` each have a test that triggers and one that does not (§18.17); masking rules cover output, trace, errors and cache (§18.12); the cost pack reports a summary of tokens (reasoning included) and cost; a CI allowance for paid calls takes effect only when the environment variable and the Suite both allow it, satisfying only one is still refused, and the allowance is written into `run.json` (§18.13); a suspected secret in a dataset makes loading fail, and after an exemption per match it passes and is written into `run.json` (§18.12) |
| E2 Retrieval pack and comparison | Retrieval pack (evidence hit, ranx metrics, latency), setup sharing and warm-up, `compare` (§18.1–§18.3), gate (CLI and pytest), baseline, cross-check of ranking metrics; **migrate lite-rag** | Per the lite-rag entry in §18.16: per-question `answer_rank` and `doc_rank` are the same, aggregate metrics differ by ≤ 1e-9; McNemar and bootstrap are checked against hand-computed examples; an incompatible comparison is refused for gating; the gate's exit code follows §18.4; gate semantics follow §18.19 (`min`/`max`, `max_drop` with and without `require_significant`, repeated runs) with a test for each; two configs with the same `setup_identity` call setup only once, the warm-up is not timed and not recorded, and teardown comes after the whole group has run (§18.18); the self-written metrics and ranx agree in tests; lite-rag is changed to call lite-eval |
| E3 Viewer | Read-only API, SQLite index (§18.15), the frontend pages (§10.1), Annotation (§18.9, §18.10), HTML export, packaging into the Python package (§18.14) | Playwright e2e: list runs → run detail → compare → case detail → triage a miss; add a "label wrong" on the datasets page; the trends page shows a baseline switch; both exported HTML kinds, single run and comparison, open without a server; the Viewer refuses non-loopback addresses, non-loopback Host and Origin, and writes without a token, and no response carries a CORS header (§18.10); export uses the masking rules of the run record, and when case content contains `</script>` the exported HTML is not cut off and runs no script (§18.12); two Viewers cannot use one directory at the same time; files and index run the same read tests; after installing the wheel in a clean venv, `lite-eval view --check` succeeds |
| E4 Memory pack and integration | Memory pack; HTTP and subprocess Targets (§18.11); LongMemEval and LoCoMo loaders; **migrate lite-mem's two-hop set and llm-steward's recall gate** | Item-by-item identical per the lite-mem and llm-steward entries of §18.16; llm-steward's `-m eval` switches to lite-eval's gate; every failure of the HTTP and subprocess Targets (status code, timeout, oversize, non-JSON, process exit) has a test; with `concurrency=4` the subprocess Target matches each reply to the right case, and a reply whose `case_id`/`repeat` does not match is recorded as `invalid_output` (§18.11); the two loaders are tested for conversion results and licence records using their own small sample files; LongMemEval-S runs through on a small subset with a local model (opt-in marker) |
| E5 Model judges (E5a lite-eval, E5b lite-rag S13) | Judge scorers, calibration flow; the three packs judging, extraction, grounded; KB export | Agreement rate measured on the calibration set; constructed counter-examples are identified correctly; a judge parse failure is recorded as a scoring error; an uncalibrated judge cannot be referenced by a formal Suite; the KB export is generated from a fixed sample run, is word for word the same as the committed expected file, and passes the KB's `check.mjs` (the export format states which schema version of the KB README it matches) |
| E6 Agent (later) | Agent pack; Inspect AI bridge (optional) | To be decided when an agent application needs testing |
| E7 Close-out | Whole-repo `/simplify` and Codex review; README; release form | Review conclusions are on record |

### Stage records

(When each stage is finished, add an entry here: what was done, what /simplify changed, Codex's conclusions, the reasons for what was not accepted.)

- **Design (2026-11-13, done in an llm-steward session)**: wrote the design document and the agent guides at each level. Documents only, so no /simplify.
  - Codex's first round raised 6 high and 13 medium priority issues: repeated runs, "identical numbers" in migration, exit-code priority, the safety and concurrency of Annotation writes, details of HTTP and subprocess Targets, the scope of masking, cache keys and versions, statistics details, run_id ordering, frontend packaging, the SQLite index, the CI billing guard, hashing, recording of failures, acceptance coverage, comparability. All accepted and written up as §18, with matching changes to §2, §5, §6, §10–§12 and §16.
  - Codex's second round raised 4 more issues: the cache key lacked the dataset and corpus hash; HTTP and subprocess Targets had no way to declare versions; the Annotation correction chain could fork; the guide and §18.13 worded things differently. All accepted.
- **Design review, third round (2026-11-13, inside the lite-eval repo, before starting work)**: Codex did a read-only review of the whole design and raised 12 items (4 high, 6 medium, 2 low).
  - Accepted:
    - subprocess Target versus concurrency conflict → §18.11 runs serially, replies carry `case_id`/`repeat`, and the process is restarted after a timeout;
    - setup sharing had no owner → new §18.18 (grouping, leader/follower, teardown order, read-only, warm-up);
    - gate semantics unclear → new §18.19, and `Score.value` fixed to [0, 1] (§5.5);
    - percentiles defined only in the migration section → made uniform in §8.1;
    - the stage close-out said "commit, push" → changed to after the owner's agreement;
    - which masking rules the export uses, and escaping of embedded JSON → §18.12, and `run.json` records `redact` (§5.6);
    - the dataset secret exemption was too wide → §18.12 exempts per match;
    - the risk of an explicit `cache: on` → §18.5 every run carries a warning;
    - gaps in acceptance → §16 E1–E4 add the CI allowance path, secret scanning, setup sharing, gate semantics, CORS, export escaping, subprocess concurrency.
  - Partly accepted: cross-origin reads on the read endpoints. The browser's same-origin policy already blocks cross-origin reads, and DNS rebinding is blocked by the Host check; only "send no CORS headers" and its test were added (§18.10), and no token was added to read endpoints.
  - Not accepted: the degenerate case of the bootstrap. When the interval is [0, 0], the rule "significant only if the interval excludes 0" naturally judges it not significant; NaN is already blocked by the canonical JSON's `allow_nan=False`. No new rule is needed.
  - v1 scope (Codex suggested cutting to the core plus a basic Viewer): set at E0–E3, with the reason written at the start of this section.
  - Also corrected: R12 and §18.13 aligned; `run(input)` in §4 changed to `run(case)`; the rule numbering in `backend/AGENTS.md`.
- **E0 Environment (2026-11-13)**:
  - What was done:
    - backend: uv project, Python 3.12, hatchling; all dependencies locked to exact versions.
    - `lite-eval` CLI: only `--help` and `--version`; commands are added in the stage that implements them.
    - `tests/unit/test_dependencies.py`: locks the third-party APIs that later stages will use with hand-computed values, checked against the installed source.
    - `tests/unit/test_packaging.py`: the wheel and sdist carry LICENSE.
    - frontend: a single-package pnpm skeleton, a placeholder page and one component test.
    - CI: two jobs, backend and frontend, actions pinned to a commit SHA, `permissions: contents: read`.
    - KB: created `projects/lite-eval/` with the decisions table and `reference/dependencies.md`; ports 8720 and 8721 registered in `ports.md`.
    - Every new test was seen failing once.
  - Design change: the E0 row httpx → httpx2.
    - Reason: openai SDK 3.50 depends on httpx2; Starlette 1.10's TestClient prefers httpx2.
    - One HTTP client per process: the ruff banned-api rule (TID251) forbids `import httpx`, and `test_lockfile_has_one_http_client` confirms it is not in the lock file.
  - /simplify accepted 9 items:
    - de-duplicated the CLI help text;
    - merged hatchling `artifacts` into `[tool.hatch.build]`;
    - mypy also checks `tests`;
    - the httpx check changed from a runtime assertion to a lint rule plus a lock file test;
    - `tests/conftest.py` pins `TERMINAL_WIDTH`, so CLI tests no longer merge whitespace;
    - removed a uvicorn test that could assert nothing (E3 verifies with `view --check`);
    - the frontend build no longer runs tsc twice;
    - CI's pnpm version is read from `packageManager`;
    - added `backend/README.md` as the package description.
  - /simplify not accepted:
    - the ranx test takes about 5 seconds: it is the real cost of numba's first compile, and turning it into an opt-in marker would leave the API unprotected;
    - the setup-uv cache: v10.3.3 defaults to `enable-cache: auto`, which is already on for GitHub-hosted machines;
    - repeated version numbers in the KB dependency page: that page is exactly the record of "which version, checked on which day".
  - Codex raised 4 items, all accepted:
    - actions changed to be pinned to a commit SHA;
    - the wheel and sdist lacked LICENSE: first wrote a failing `test_packaging.py`, then copied `backend/LICENSE`, and the test also asserts the two copies are the same;
    - removed a meaningless `binomtest(0, 1)` assertion;
    - stage status and stage record aligned.
  - The first CI run was red (`3484fe0`): `test_help_exits_zero_and_names_the_tool` failed.
    - Cause: when `GITHUB_ACTIONS`, `FORCE_COLOR` or `PY_COLORS` is set, typer 0.29.1 forces coloured terminal output, and the ANSI codes split `Usage: lite-eval`; locally the output is not a terminal and has no colour.
    - Reproduced the failure locally with `GITHUB_ACTIONS=true uv run pytest`.
    - Fix: `tests/conftest.py` sets `_TYPER_FORCE_DISABLE_TERMINAL=1`. typer reads this switch at import time, and it sits in the same place as `TERMINAL_WIDTH`.
    - After the fix the tests pass under all three variables.
- **E1 Contracts and execution (2026-11-13)**:
  - What was done:
    - Contracts: Case, Output, Trace, Score, RunRecord, Annotation (§5); canonical JSON and hash (§18.6); dataset directory, secret scan on load and per-match exemptions (§18.12).
    - Target: Python Target and FakeTarget.
    - Scorers: 7 deterministic scorers, plus the all_of, any_of and weighted combinations.
    - Two-step runner: cache, repeats, concurrency, timeout, retry, a budget shared by the whole Suite, self-checks, masking, CI billing guard.
    - Run directory (atomic writes), `run` and `rescore`, terminal report, cost pack.
    - `backend/examples/`: a minimal Python Target with 3 cases.
    - New §18.20, recording the integration and runner details the design did not fix.
  - Verification:
    - pytest 153 passed; also passes under `CI=true GITHUB_ACTIONS=true`.
    - Every new test was seen failing once: a script broke 60 places, and every one made its matching test fail.
    - The break script's first round had two "false greens": Python decides whether a `.pyc` is stale by the source file's modification time (to the second) and size, so two breaks of the same size within one second tested the old bytecode. The script was changed to write no bytecode and to clear `__pycache__` each time.
    - A skip in CI makes it fail (`tests/conftest.py`), actually tried: with `CI=true` there was a skip, and the exit code was 1.
  - /simplify's four reviews found 4 bugs, each fixed by writing a failing regression test first:
    - a cache-hit output was not masked by the current rules (it would leak after a Suite added a rule);
    - the budget was counted per config, so 3 configs could spend 3 times the budget;
    - FakeTarget inferred the repeat index from call order, which misaligns on partial cache hits; changed to a context variable `current_repeat` set by the runner;
    - the same combined scorer written differently got different ids; changed to normalise before generating the id.
    - While fixing, another existing test caught a bug where `from_spec` iterated a generator twice.
  - Simplifications /simplify accepted:
    - atomic writes and JSONL reading merged into `files.py`, masking rules compiled only once, masking of Output and Score moved into `Redactor`;
    - `execute` split into two steps, running one config and producing the record, and self-checks split into those that look only at generated output and those that depend on scores (`SCORE_DERIVED`);
    - unique config names, unknown pack and the `redact` form are all checked when loading the Suite;
    - removed unused code, and scorer ids are cached instead of recomputed.
  - /simplify not accepted:
    - changing p50 to nearest-rank: §8.1 fixes it as `statistics.median`, so the numbers match when migrating lite-rag;
    - moving cache reads and writes into threads: at the scale of a few thousand cases, thread switching costs more than it saves;
    - running several configs concurrently: it would change the semantics of concurrency, budget and setup, and needs a separate design decision;
    - streaming writes of the run directory: not needed for now.
  - Codex raised 5 items:
    - 4 accepted, each with a failing test first:
      - when the budget ran out, the failed result of the last transport error was dropped;
      - `rescore` did not work when the Suite was outside the root directory, so it now records a relative path containing `..`;
      - `token=abc}` replaced the closing brace along with the token;
      - duplicate outputs in a run directory silently overwrote each other, so they are now rejected on read.
    - 1 not accepted ("`max_calls` can be exceeded under concurrency"): there is no `await` between checking the budget and counting, and asyncio is single-threaded, so nothing can be interleaved. Added `test_max_calls_is_exact_under_concurrency` to prove it.
- **E2a Retrieval pack, comparison, gate (2026-11-14, lite-eval repo)**: E2 is split in two: E2a is done in lite-eval, E2b migrates lite-rag.
  - What was done:
    - Pack registry and the retrieval pack; retrieval scorers `hit@k`, `doc_hit@k`, `mrr@k`, `ndcg@k`, `recall@k`, with the evidence-sentence judgement word for word the same as lite-rag's `judge` / `_norm`, cross-checked with ranx 0.3.21, and `--crosscheck` provided.
    - Setup sharing and warm-up (§18.18).
    - Comparison (McNemar, bootstrap, comparability, `within_noise`); gate and baseline; the commands `compare`, `baseline set`, `run --gate`, and `assert_gate`.
    - Contracts only gained fields: `RunRecord.case_hashes`, `ScorerRef.verdict`, `Comparison` and so on, and `RunRecord.gate` became a typed `GateSpec`.
    - New §18.21 records the integration details; `backend/examples/` gained a retrieval example.
  - Verification:
    - pytest 244 passed; the break script turned E2a 43/43 and E1 64/64 red.
    - Walked through it by hand with the retrieval example: two configs → `compare` → `baseline set` → `run --gate`, and exit codes 0 and 1 were as expected.
  - Bugs found and fixed during implementation (each with a failing test first):
    - when the drop was exactly `max_drop`, floating-point error made the gate fail, so a tolerance of 1e-9 was added;
    - a binary scorer's `:pass_rate` duplicated the value;
    - pass rate was inferred from the data, so one errored case made MRR sprout a meaningless `:pass_rate`; changed so the scorer declares it.
  - /simplify (four reviews):
    - 3 bugs:
      - compare guessed binary from the values, so a continuous metric whose values were all 0/1 on a small dataset got the wrong test; changed to record `verdict`;
      - a relative gate on a cost metric only errored after the run had spent the money; changed to reject it at Suite load;
      - the gate comparability rule was written twice and had drifted apart; merged into one `gate_problems`.
    - Improvements accepted: bootstrap changed to resample the differences and in chunks (the fixed-seed result was pinned first and did not change); the gate uses the scores the runner already holds, and the baseline is read only once, skipping `outputs.jsonl`; retrieval metrics use one formula for both modes; `normalise` is cached; the cross-check compares against the values in the record; `assert_gate` reuses the terminal format; `root_of`; test helpers merged.
    - Not accepted:
      - leaving the `warming_up` context variable in the protocol (already written into the docs);
      - `share_setup_from` is not split out as a separate sub-protocol for now;
      - ranx is not merged into a single call: `--crosscheck` is an optional diagnostic;
      - scores are not pre-grouped per scorer: it would not save much time at this scale.
  - Codex:
    - Accepted 2, each with a failing test first:
      - a follower's teardown raising would skip the leader and replace the original error;
      - when the warm-up raised a `TargetError` carrying usage, the cost was lost.
    - Not accepted 1: "also report `within_noise` when both the difference and the spread are 0". The spread of a single-run run is always 0, so every tie would warn, which carries no information; §18.1 already says to warn only when the spread is > 0.
    - Of the three coverage gaps Codex named, two already have tests: a threshold exactly equal is covered by `test_a_drop_within_max_drop_passes`, and id de-duplication and falling back to doc are covered by `test_relevance_mode_metric_values` and `test_relevance_mode_prefers_a_hit_id_over_its_doc`; the third (teardown on the error path) was covered by a test added with the fix above.
- **E2b Migrate lite-rag (2026-11-14, lite-rag repo, as lite-rag's S12)**:
  - The dependency rule changed (AGENTS rule 13, decided by the owner 2026-11-14, `b1a49a9`): runtime dependencies declare a version range, the lower bound is the oldest version the tests pass on, checked by the CI job `lowest`, and the upper bound is the next major version; `uv.lock` still locks exact versions. Cause: lite-rag's docling extra requires typer < 0.28, which cannot be resolved together with the exact `typer==0.29.1`.
  - What was done:
    - lite-rag gained an optional extra `eval`, locking lite-eval by commit (`b1a49a9`); `src/lite_rag/eval.py` was rewritten as the adapter `LiteRagTarget` (§5.3), and the self-written `judge`, `metrics`, `_norm`, `_nearest_rank`, `run_configs` and `eval/crosscheck_ranx.py` were deleted.
    - The `literag eval` arguments and table are unchanged, with `--root` and `--crosscheck` added; the golden sets and corpora were converted to lite-eval datasets, each config is one lite-eval run, and the status and warnings of each run are printed under the table; a lite-eval error is a single error line with exit code 1 in the CLI.
    - Configs with the same index share one setup (§18.18); setup computes the query vectors without timing; each run records its latency definition in `target.info.latency_scope`.
  - Verification (the two layers of §18.16):
    - Scorers: 2000 random hit sets were fed to the frozen old `judge` (`tests/legacy_eval.py`) and to lite-eval's scorers, and the per-question ranks were the same;
    - End to end: in the same process and the same store, 4 golden sets had the same per-question `answer_rank` and `doc_rank`, and the four metrics, overall and grouped by `kind`, differed by ≤ 1e-9 (FakeEmbedder in CI; bge-m3 under `-m ollama`, passes locally);
    - three configs were run for real on this machine and matched the lite-rag S5 baseline item by item;
    - lite-rag pytest 480 passed, and the break script turned 19/19 red.
  - The /simplify and Codex conclusions are in the S12 implementation notes of the lite-rag design. Rejected items that concern lite-eval:
    - `run_suite(targets=…)` is not made public: lite-rag uses `runner.execute` directly (its targets share one store and embedder, which `python: module:factory` cannot express); the public API will be decided when a second user appears, and until then lite-rag is pinned by commit and runs the equivalence tests at every upgrade;
    - lite-eval does not call teardown after a failed setup: that would change the Target protocol; an adapter whose setup can fail halfway cleans up inside setup itself (lite-rag does exactly that).
- **E3a Viewer backend (2026-11-14, lite-eval repo)**: E3 is split in two: E3a the backend, E3b the frontend, e2e and packaging.
  - Decided by the owner (2026-11-14): case content is fetched from the dataset by hash and not stored again in the run directory; `baseline set` appends to `baselines.log.jsonl`; `lite-eval triage` is done together in E3.
  - What was done (details in §18.22):
    - reading and writing of annotations (§18.9);
    - the SQLite index and the two kinds of catalog, file and index (§18.15);
    - `views`, plus FastAPI's read-only API and annotation writes, with security rules per §18.10;
    - `view` (including `--check`), `triage`, `report --html`;
    - committed `backend/openapi.json`, and a test fails when it is out of date.
  - Verification:
    - pytest 322 passed; also passes under `CI=true` and in a venv installed at the lowest declared versions (same as CI's `lowest` job).
    - The break script turned 63/63 red.
      - One equivalent mutation was deleted, because: with append-only writes under a lock, "the one not superseded" is always the last one written for that key.
      - Two mutations would make `view` really start and hang: the matching tests were changed to fail as soon as the server starts, and the script kills the whole process group on timeout.
    - Walked through the API with lite-rag's real `eval/lite-eval/`: 3 runs, misses, case content fetched by hash, a significance comparison, trends.
  - Problems found and fixed during implementation:
    - The spec generated by fastapi 0.133 with pydantic 2.9 (the declared lower bound) writes an extra `enum` for a `Literal` with a single value. When exporting the spec, a single-element `enum` that duplicates `const` is removed, so the locked version and the lowest version produce the same file.
  - /simplify (four reviews):
    - 3 bugs, each with a failing test first, or with a note on why it could not be tested:
      - in a partial run, cases that were never generated did not count as misses and their scores were missing, which breaks rule 4. Per-case results are now computed by `analysis.summary.case_results` with the same definition the run summary uses, recorded as `not_generated` and scored 0;
      - export masked all of the data blindly, which rewrites ids such as `case_id` and also breaks §18.12's "datasets are not masked". It now masks by type, with the same code as the Viewer;
      - the baseline log had no fsync after appending. It now shares `files.append_jsonl`; fsync itself cannot be observed by any test.
    - Improvements accepted:
      - loading and assembling are separated: export and triage load each run only once (before, every case reloaded the run, all datasets and the other runs);
      - finding a dataset by name reads only the yaml and the cases and no longer hashes the corpus; for "other runs", run.json is read first and outputs are not parsed if the hash does not match;
      - the two catalogs share one query path (before, SQL and Python each had a filter);
      - added `annotate()`, `store.run_path` / `read_record`, `errors.NotFound`;
      - removed `_Only`, assorted limit sentinel values and dead code; address validation became a pure function and is tested directly; test helpers were gathered in `tests/helpers.py`.
    - Not accepted:
      - comparing the OpenAPI spec conditionally by version: it would weaken the check, and CI does not allow skipped tests;
      - changing `report A..B` to two arguments: it is the public syntax fixed in §11.2, left for the owner to decide;
      - a query parameter model, SQL pagination, a background refresh thread, a dataset summary cache: at a scale of a few hundred runs the gain is very small.
  - Codex: 1 item held up and was fixed (red then green): the comparison export looked for both runs under the first run's eval root; each is now loaded from its own directory. In the other areas (security, locking, index, escaping, masking, per-case definition) no problems were found.
- **E3b Viewer frontend, e2e, packaging (2026-11-14)**: E3 is complete, and **v1 (E0–E3) is complete**.
  - What was done (details in §18.22):
    - seven pages: Runs, run detail, case detail, compare, miss triage, datasets, trends;
    - all data goes through `src/data/source.ts` (two data sources, the live API and the exported page);
    - Playwright e2e runs on fixtures generated by the fake target, on port 8722;
    - `scripts/build_frontend.py`; CI gained a `package` job (install the wheel in a clean venv, then run `view --check`) and a `release` job (publishes when a `v*` tag is pushed).
  - Visual:
    - The dense-data direction was specified by the task. The owner answered two rounds of interview, written up as `PRODUCT.md`;
    - the direction notes are in `.impeccable/surfaces/frontend-src.md`; `DESIGN.md` and `.impeccable/design.json` were compiled by Impeccable's documenter from the built interface;
    - the status colours were verified in both themes with dataviz's validation script; the signature designs are the interval bar and the case barcode.
  - Impeccable finish review: three rounds.
    - The first round had 8 groups of issues, all resolved after the first two rounds: the diagonal hatching and hollow dots of the interval bar, the verdict and difference side by side at phone width, integer ticks on the trend chart, a fixed first column and scroll fade, the information on the case and triage pages, the layout of run detail;
    - a regression introduced by the fixes (the page was 20px wider at phone width) was also resolved;
    - the last item (the direction notes have no seed: the direction was specified by the task and not drawn) was closed after the owner explicitly accepted it on 2026-11-14.
  - Verification:
    - backend pytest 328 passed (three environments: locked versions, `CI=true`, lowest declared versions);
    - frontend: 14 unit tests, 8 e2e tests (acceptance flow, offline export, themes);
    - break script: frontend 20/20 red (unit + e2e), and the 7 tests the backend added this round 7/7 red;
    - after installing the wheel in a clean venv, `view --check` returned two 200s; a screenshot of the bootstrap interval chart was taken with lite-rag's real data.
  - /simplify (four reviews), accepted:
    - bugs:
      - silent truncation when there were more than 1000 cases: the limit was raised to 100000, and case detail gained previous / next;
      - the exported page's filters only half worked: the exported page no longer offers filters;
      - the direction of a change was inferred by the browser: changed to `CaseChange.direction`;
    - the export data became a pydantic model written into OpenAPI, and the backend supplies the start route;
    - after saving an annotation only the related queries are invalidated; run-related queries never go stale; the case barcode is memoised;
    - shared `useShortcuts`, `rowProps`, `queries.ts`, `Select` / `KeyHints` / `WarningsList` / `RunLink` / `ContentNote`;
    - `light-dark()` lets the colour variables be written once; the "pass" cell raised to 3:1;
    - CI's e2e does not build again, installs only the `[server]` extra, and caches uv and Playwright's browsers.
  - /simplify not accepted:
    - writing the e2e expectations into a manifest generated by the fixtures: when the fixtures change, the e2e fails loudly anyway, so the gain is low;
    - moving the case barcode out of the case table and making `Th` / `Td` table components: small gain;
    - a dedicated options endpoint for the trends page: memo is enough at the current scale.
  - Codex:
    - Accepted 3, each with a failing test first:
      - rows of the trends table lacked run flags: `TrendPoint` gained `status`, `warnings`, `synthetic`;
      - very small numbers displayed as 0 (the weight of one case, a significant but tiny difference): when three decimals would round to 0, two significant digits are used;
      - CI did not check whether the sdist carries the frontend.
    - Not accepted 1: "pressing X while focus is on a checkbox toggles that row". The checkbox is an `input`, which the keyboard guard ignores, and `format.test.ts` was added to pin this behaviour. That J/K still move the list while focus is on a link is intentional.
- **E4a Memory pack, HTTP and subprocess Targets, external benchmark import (2026-11-14, lite-eval repo)**: E4 is split in three: E4a lite-eval itself, E4b the lite-mem migration (its S11), E4c the llm-steward migration (its P8).
  - What was done (details in §18.23):
    - memory pack: `hop<n>`, `all_hops`, with the hit rule copied word for word from lite-mem's `_has`; evidence recall reuses `recall@k`, and forbidden content reuses the existing `must_not`;
    - HTTP Target (a JSONPath subset, `${ENV}` headers, sandboxed templates) and subprocess Target (serial, restart when out of sync, restart only once, a run is partial after giving up, stderr goes into the run directory, process group);
    - `lite-eval dataset import longmemeval|locomo`; `DatasetRef.license` (a field only added, old records still read); `openapi.json` and the frontend client types regenerated;
    - the subprocess example `examples/command_target.py` and the test that runs it.
  - Design changes:
    - external benchmarks changed from "a loader in the Suite" to an import command, for the reasons in §18.23;
    - the lite-mem and llm-steward entries of §18.16 were corrected to match the code (data size, `follow_up` is a parameter of the whole run, `.lower()` is not casefold, TestClient, only the pass without rerank goes through the gate); after the llm-steward migration, a separate assertion that no case errored is needed;
    - the memory row of §7: extraction and update deferred, refusal moved to E5 (decided by the owner 2026-11-14).
  - During E4a the owner changed the scope of E4b: LongMemEval runs in full on all three variants (S, M, oracle), not only the first 20 questions of S.
  - Break script: 68/68 red, source restored after the run. Three problems in the tests themselves turned up along the way, all fixed:
    - the runner's serial gate had no test (removing it left everything green): added a test with "each reply 0.3 seconds, timeout 1 second, 6 cases, concurrency 4";
    - when the Target's lock was removed, the concurrency test hung instead of failing: `gather` returns on the first error, teardown collided with calls still restarting the process, and the leftover subprocess kept the event loop from closing; changed to `return_exceptions=True`;
    - the example test depended on `python` being on PATH: CI's lowest job calls the venv's interpreter directly and it is not on PATH; the test puts the interpreter's directory at the front of PATH (the example states it runs under `uv run`).
  - /simplify (four reviews), accepted:
    - LoCoMo conversations are stored once in `corpus/` and no longer copied into the roughly 200 questions;
    - the file hash is computed in a stream during import, so the 265 MB original and the parsed result are not both held in memory;
    - the subprocess Target uses an "in progress" flag in place of the `_dirty` that was set in four places; the two restart branches merged; the escalation in `_stop` was written as a straight line; not ready after a restart counts as one exit (test added);
    - the HTTP and subprocess Targets share `SpecBase` / `SpecIdentity`; the HTTP Target no longer times itself (the runner already measures wall-clock time) and decodes only the first 500 bytes of a reply, for the error message;
    - loaders dropped the registration function that was deferred to avoid a circular import (`_common.py`); LoCoMo no longer accepts a parameter it ignores;
    - the Target's optional members are all listed in the `Target` docs; tests share `Sleeps`, `run_prepared`, `output_errors`, `demo_dataset`; the memory scorer lowercases each memory only once.
  - /simplify not accepted:
    - changing `serial` to `max_concurrency: int`: there is no second use yet;
    - moving `_builder` to `targets/__init__.py`: `suite.py` imports `targets.http` / `targets.command`, which would form a circular import;
    - caching the serialised case for each request: it helps only when `repeats > 1`, and is negligible against the Target's own latency.
  - Codex: 8 items.
    - Accepted 5, each with a failing test first (all 7 mutations turned red):
      - a subprocess started by a wrapper command was still alive after restart and teardown: changed to its own process group, with signals sent to the group. While writing the test it also turned out that asyncio's `Process.wait()` does not return until the pipes are closed, so a child that inherited the pipes would stretch the wait until it ended itself; the test's child process therefore connects its stdin and stdout to `/dev/null`;
      - a process was left behind when startup failed: `_start` now kills the process on failure first;
      - when the process exited between two cases, the next case was recorded as failed: now it checks before sending, counts one exit, and sends the case to the new process (Codex's suggestion of "do not count" was rejected: a program that exits after answering every question would restart forever);
      - once stderr was full, the `[lite-eval]` note lines were dropped too: note lines are not subject to the limit;
      - there was no test for when HTTP `timeout_s` is not set: added a test that the Suite timeout cancels the request.
    - Not accepted 3:
      - "the runner must catch `CancelledError` on timeout cancellation": the in-progress flag already guarantees a restart before the next case, and on cancellation the runner's `finally` tears down as usual;
      - "use `has_answer` to decide LongMemEval's abstention questions": `has_answer` marks evidence turns, and the official marker for abstention questions is an id ending in `_abs`;
      - "`[*]` on an empty list should raise an error": no match is a legal retrieval output, written into §18.23.
  - Security review after the commit (`0f1a056`): when a server echoes request headers, the resolved secrets could enter the output or error messages. A failing test was written first (two kinds of echo, `/reflect` and `/reflect-401`), then the HTTP Target was changed to replace uniformly; both mutations turned red.
  - Second security review (`69ee17f`): the error page was truncated first and replaced after, which leaves the first half of a secret that straddles the truncation point; a secret that was JSON-escaped or URL-encoded in the error page was not replaced either. Failing tests were written first (four kinds of echo: truncation, JSON, `quote`, `quote_plus`, checking whether the first 6 characters appear); changed to replace the whole text first and then truncate, and to replace these encoded forms too; all 4 mutations turned red.
  - Verification (last time before the commit):
    - backend ruff, format and mypy pass; pytest 383 passed (three environments: locked versions, `CI=true`, lowest declared versions; the lowest version calls the venv's interpreter directly, as CI does);
    - `openapi.json` is up to date; frontend typecheck, lint, 14 unit tests and build pass (only the generated client types changed);
    - break script 68/68 red, source restored.
- **E4b lite-mem migration (2026-11-14, the lite-eval part; for the lite-mem part see its design §12 S11)**:
  - What changed in lite-eval:
    - import now reads and writes item by item (`iter_json_list`, §18.23). Measured on the real S (277 MB): reading the whole file peaked at 2.4 GB, streaming 313 MB and 3 seconds; reading M (2.7 GB) whole would take about 24 GB. When a parse fails and the file is re-read, the buffer at least doubles (found by /simplify: growing in 1 MB steps meant a 5 MB question was parsed from the start five or six times).
    - Each LongMemEval question's history goes into `corpus/<question_id>.json`, and the case keeps only `haystack: <question_id>`, so the runner does not need to read all of M into memory. This is a deliberate change to the E4a dataset format, and the input expectations in the original tests were changed accordingly.
    - With `--limit`, the note says "skipped N abstention questions before the cutoff" (Codex: abstention questions after the cutoff were not counted).
  - lite-mem side (S11): the two-hop eval runs on lite-eval. 8 groups of configs, compared in the same process with the frozen original script: the hits for every question are exactly the same (FakeEmbedder in CI, bge-m3 under `-m ollama`), and the summary numbers equal the 2026-11-12 record. LongMemEval tests only the retrieval layer, and the results are in lite-mem's KB.
  - Security review (`69ee17f`, after the commit): both items held up, see §18.23 and the last item of the E4a record above.
  - /simplify (four reviews). Accepted in lite-eval: buffer doubling (added a test that counts reads, and it turns red when broken); the logic that prefixes the file name to error messages was simplified (`--include-abstention` is checked before reading the file); the call site now states that `notes` is complete only after reading finishes. What lite-mem accepted is in its S11 record. Not accepted:
    - `mkdir(exist_ok=True)` on every corpus file written: the cost is negligible;
    - turning LongMemEval's abstention count into a separate statistics object: stating the order at the call site is enough.
  - Raised by /simplify and left to the owner:
    - **whether the corpus gets a secret scan**: from E1 on only `cases.jsonl` is scanned and `corpus/` never is (LoCoMo's conversations and lite-rag's documents are the same). Once LongMemEval's history moved into the corpus, it is not scanned either. E4b at first wrote this down as a benefit; it has been changed to state the fact (§18.23). Decided: no scan, and the reason is written into §18.12.
    - **general "known value" masking**: the HTTP Target recognises only a few encodings (as is, JSON-escaped, `quote`, `quote_plus`), and the subprocess Target's stderr does not mask by value the variables that `env_allow` lets through. The deeper approach is to give `Redactor` a "known value" rule: the Target declares the secrets it resolved, and the runner masks them together at the places already masked uniformly (output, artifacts). The percent-encoding case variants Codex raised belong here too. Decided: do it as a separate small stage after E4c (it changes the runner, so plan mode first).
  - Codex:
    - Accepted 1 (failing test first): the note on the abstention count under `--limit`.
    - Not accepted 1: "`[1 .2]` treats `1` as a complete number at a buffer boundary": `1` is a complete JSON number anyway, and reporting `.2` as "after item 1" is correct.
  - Design and code did not match, corrected: the table in §13 wrote ranx as a "direct dependency", and §7 and §15 wrote "ranking metrics (ranx)"; in the code the ranking metrics are computed by ourselves, and ranx is used only in the optional cross-check (found when the owner asked about Ragas).
  - Verification (last time before the commit):
    - lite-eval: ruff, format and mypy pass; pytest 402 passed (three environments: locked versions, `CI=true`, lowest declared versions); `openapi.json` unchanged;
    - lite-mem: ruff, format and mypy pass; pytest 358 passed (including PG); the `-m ollama` two-hop equivalence test passes;
    - all break checks turned red and the source was restored; LongMemEval oracle and S were run to the end with the final code, and the temporary directories have been deleted.

- **E4c llm-steward migration (2026-11-14, llm-steward's P8; details in its design §11 P8)**: the `-m eval` recall@8 gate runs on lite-eval. The test still loads the corpus once through the API as before, `StewardTarget` wraps this tenant (`setup` does nothing, followers share it), and hits map back to the corpus keys; `plain` goes through the gate (≥ 0.92), `rerank` is only reported; `degraded` is recorded as `invalid_output`. It is 0.960 on both SQLite and PG (rerank 0.980), the missed queries are the same as before, and per-question results match the frozen `_recall` (CI also has a unit test with forged replies). lite-eval is pinned by commit in llm-steward's dev group (`8bc9301`, 0.1.0). The lite-eval code was not changed.
  - lite-eval changes found during the migration and left for the small stage below:
    - gate rules cannot be limited per config: a gate on the suite is declared for all configs, and llm-steward's `rerank` run record carries it too, but nobody checks it;
    - the gate cannot govern the number of errored cases: to require "no errors", a separate assertion outside the gate is the only way (both llm-steward and lite-mem's LongMemEval need it).
- **E4d Mask known secrets by value, gate rules limited per config, error count as a gate metric (2026-11-14, lite-eval; plan approved by the owner)**:
  - What was done (details in §18.11, §18.12, §18.21):
    - `redact.KnownValues` and the Target's optional `secrets()`: the runner builds a redactor with known values for each config group and uses it at generation (before writing the cache), scoring, artifacts, run warnings, the Target config in the record, the announce line, and the error messages of setup / teardown; `RedactConfig.known` records only names. The HTTP Target's `_Masker` was deleted and replaced by declaring `secrets()`; the subprocess Target declares the allowed variables whose names look like secrets (or `secret_env`). Both Targets mask the whole raw text on receipt first, then truncate or validate.
    - `GateRule.configs`; three run metrics `errors`, `error_rate`, `scoring_errors` (absolute gates only); `GateOutcome.applied`. Contracts only gained fields, old records still read (tested), and `openapi.json` and the frontend client were regenerated.
  - Where it differs from the plan (all written into §18):
    - the subprocess Target was planned as "every allowed variable counts as a secret, with a few names excepted"; changed to "only names that look like secrets count, or those explicitly listed in `secret_env`": treating everything as a secret would mask ordinary values such as `OLLAMA_HOST` in every output and silently change scoring (/simplify);
    - the HTTP Target was planned as "mask only before truncating the error page"; changed to "mask the whole reply as soon as it arrives": after moving it behind the runner, pydantic's validation error shows only 24 characters on each side of the value, the runner cannot recognise it, and both ends of the secret were left behind (a regression found by /simplify, with a failing test first).
  - /simplify (four reviews) accepted: the two items above; the three places in the subprocess Target that truncated before masking (quoting the prepare line's first 200, truncating the reply to 500, pydantic's message); setup / teardown errors are not masked, and when there are known values the exception chain is not kept either; a reply that is not UTF-8 is recorded as `invalid_output` without replacement characters (both Targets have tests); the error count is computed uniformly by a property of `RunSummary` (shared by the gate and the Viewer index); the subprocess builds its environment in one place only; `with_known` uses `dataclasses.replace`; `_forms` keeps only (form, marker). Not accepted: the efficiency review measured 2.4 µs more per string and about 24 ms per config group, which is not worth changing the replacement order; `GateOutcome.applied` stays a count (it may be displayed later); merging the suite spellings in tests (it is only duplication in test code).
  - Codex: accepted 3, each with a failing test first: the Target config in the record and `describe()` are masked too; a config error when the gate judged no run at all; the marker is not rewritten by a later short secret (private-use character protection). Not accepted 1: `rescore` handling records from before E4d (reason in §18.12).
  - Break script: 37/37 red, source restored (the first round had 2 that did not turn red, so tests for "a secret in a JSON key" and "a secret in scoring evidence" were added; later a missing UTF-8 test was found in each of HTTP and subprocess and added).
  - Security review after the commit (`9d8e815`): both items held up, with failing tests first. Other encoders' escape forms and mixed forms (`<`, `&#47;`, only partly escaped) were not masked: changed to match all JSON and HTML escapes character by character. Truncating stderr at the limit leaves the beginning of a secret: when truncating, drop the length of one longest form at the end (including the case where the previous chunk filled the limit exactly). Break script 42/42 red. The re-review of `412af4b` found two more, again with failing tests first: after truncation, trimming the tail frees room again and a later chunk would keep going; leading zeros in an entity are unlimited, so `longest()` is not an upper bound. Changed so nothing is kept after truncation, leading zeros at most 8 and each character at most 18 bytes. Break script 44/44 red.
  - Afterwards: llm-steward replaces its hand-written test assertions with `configs: [plain]` and `{metric: errors, max: 0}`, and lite-mem's LongMemEval uses `errors max 0`; each will be done as a small commit once lite-eval releases a new version and they change their dependency.

- **E5a Model judges (2026-11-18, lite-eval; plan approved by the owner, E5 split into E5a lite-eval and E5b lite-rag S13)**:
  - Decided by the owner: the judge uses qwen3:8b on the local Ollama (free), and later, on the owner's instruction, API models are added through llm-gateway for comparison; the first real judge is the grounded verdict on lite-rag's answers (E5b).
  - What was done (details in §18.24): `judges/` (spec, client, rubric, fake, target, calibration, construct); judge scorers and an async scoring phase; the calibration check, the `judge_uncalibrated` warning, and the gate refusing uncalibrated judges; the three packs `judging`, `extraction`, `grounded`; judge cost and the confusion matrix in the terminal; `lite-eval export kb`. Contracts only gained fields: `JudgeRecord.think`, `JudgeRecord.usage`, `CostSummary.judge_*`, `ScorerRef.calibrated`; `openapi.json` and the frontend client were regenerated.
  - Where it differs from the plan:
    - removed the `[judge]` extra (openai SDK): both channels are called directly with httpx2 and do not depend on an SDK. The E0 test that pinned the openai API, `test_openai_sdk_timeout_type_is_its_own`, was deleted along with the dependency (the library it pinned is no longer a dependency; it was not deleted to turn things green); no sibling repo used this extra.
    - the construction tool does not "add a forbidden phrase": that is judged by the `must_not` rule and is not a label of the grounded judge.
    - a judge cannot go into a combined scorer (combinations are synchronous, and a judge's score must also be visible on its own).
  - /simplify (four reviews) accepted: judge calls and identity are down to one copy each (`Rubric.ask`, `Rubric.identity`, so during calibration the judge sees the same prompt as in use); the calibration check moved into `judges/calibration.py`, its spelling is validated when the scorer is built, and it reads only `run.json`; in the runner the judge's switch, secrets and counts merged into one `judging()` context manager; the concurrency slot is held only during the call (budget check, call and accounting inside one slot, with backoff outside); deterministic scorers create no task; the template is read once and compiled once; the key uses `resolve_env`, and `secrets` entered the `JudgeClient` protocol; the reply is masked only on error; `_Ledger.charge` and `scorer_raised` are shared; removed the unused `is_judge` and a `Verdict.score` that is not in the schema; `KbSpec` is typed with the KB's values and moved to `suite.py`, so a mistake is reported at Suite load; export reads only the record. Not accepted: sharing one retry function between generation and judging (it would change the existing generation code, and the two sides record different error kinds; left to E7); sharing a request function between the judge client and the HTTP Target (the masking timing differs); merging the judge's `_sources` and extraction's `_items` (it would make `judges/` depend on `scorers/`); opening the judge client only once per Suite (a few more handshakes per config, negligible); putting the confusion matrix into the Viewer and HTML (frontend work, later).
  - Raised by /simplify, decided by the owner (2026-11-18): originally one uncalibrated judge made the whole gate refuse; changed to refuse only the rules that read that judge's metrics, so an exploratory judge can sit in the same Suite as existing deterministic gates (failing test first; break script 45/45 red).
  - Codex: accepted 2, each with a failing test first: judge calls were not counted in `budget.max_calls` (changed so every call is recorded in the shared ledger); `min_agreement` accepted `-1`, `NaN` and `1.5` (changed so it must be a number from 0 to 1). Not accepted 1: "the judge Target's identity does not include `base_url`, so switching servers would use the cached verdicts": leaving the host out of the identity is intentional (§18.24: the same model on another machine is the same instrument); the real gap is that the model label does not pin the digest, which exists on the same machine too after `ollama pull`, and to re-judge use `--no-cache`.
  - Break script: 41/41 red, source restored. The first round had 3 that did not turn red (the cost of an invalid reply not counted into the budget, the prompt version not in the identity, the middle number of a citation list `[1, 3, 5]` being taken as a number), and tests were added; after /simplify "a partly finished calibration run does not count" was added. Also: when the break script swapped `==` for `!=` (same length) and restored within the same second, Python's bytecode cache, which goes by modification time and size, left a broken `.pyc`, and later tests failed inexplicably; the script was changed to use `PYTHONDONTWRITEBYTECODE=1`.
  - Security review after the commit (`d4413d0`): both items held up, with failing tests first. `JudgeTarget.info()` wrote `base_url` as is into the run record, and user info or query parameters in the URL may carry a secret: only scheme, host, port and path are kept. `rescore` calls the judge again but did not go through the CI billing guard: when no options are passed it goes by the `CI` environment variable, and it refuses non-free judges before the call in the same way. Break script 44/44 red.
  - Verification (last time before the commit): ruff, format and mypy pass; pytest 515 passed (three environments: locked versions, `CI=true`, lowest declared versions); `-m kb` passes with the KB's own `check.mjs`; `-m ollama` passed a real round trip with qwen3:8b (Ollama was stopped afterwards); frontend typecheck, lint, test (14), build, e2e (8) pass.
- **E5b lite-rag S13: grounded judge (2026-11-18, lite-rag `9c9b7ac`, lite-eval 0.3.0)**:
  - What was done (details in the "S13 implementation notes" of the lite-rag design): lite-rag's answer Target (`LiteRagAnswerTarget`, which runs `literag ask`, with billing counting the answer model); the calibration set `eval/judge-grounded` (223 cases, seed 20261118, generated by a script and committed); `literag eval-answers` judges real answers with the judge from one calibration run, and refuses before generating any answer if the agreement rate is not enough. lite-eval itself was not changed.
  - The calibration set (L2): the first version had only acceptable answers and mechanical breaks, and qwen3:8b scored 262/262; only reading the real answers one by one showed that the judge missed "sounds plausible, but the source does not say it" additions. So breaks written by hand after real mistakes were added (for each question, one sentence that reverses the source and one plausible addition), together with 30 real answers labelled by reading (2 `unclear` ones kept on file and not used). The hand-written sentences and real labels were written by a model and await the owner's check.
  - Results (223 cases × 2 runs, temperature 0, both runs agree): thinking off 0.955 (plausible additions 0.774, real answers 0.900, p50 3.2 seconds); thinking on 0.982 (plausible additions 0.968, real answers 0.933, p50 11.8 seconds). Both settings miss the same two real answers that add claims on top of several sources. Conclusion: usable only as a coarse filter (use thinking on if used), not as a gate. KB card `global/llm-evals/evals/2026-11-18-lite-rag-grounded-judge.md`, and the two `results.jsonl` rows were generated by `lite-eval export kb`.
  - A lite-eval problem found (left for later): the date of `export kb` is the UTC date of the run id, so a run on a New Zealand morning is recorded as the previous day; this time the two rows were hand-edited to the local date 2026-11-18. Fixed (2026-11-18): the Suite's `kb.timezone` (§18.24), with a failing test first.
  - Codex: the first round (initial version) accepted 6 (an inserted sentence or a swapped-in answer may already be in the source; language changed from judging by text to taking the golden set's `lang`; `calibrated_judge` did not check rules; the bytes of a retrieval dataset's `dataset.yaml` must not change; the calibration run path; the language test was too loose), each with a failing test first. The second round (hand-written breaks and the real slice) accepted 3: hand-written rows repeating a question number, a real row with an unknown label, a real row with a repeated ID, all changed to raise errors and with failing tests first; not accepted 1 ("the test should prove the hand-written contradictory sentences really contradict the source": a machine cannot judge this, and this batch of sentences is marked as awaiting the owner's check).
  - Verification: lite-rag ruff, format and mypy pass, pytest 500 passed; break script 22/22 red; KB checks all pass; lite-rag CI passes; Ollama stopped.
- **E7 Close-out (2026-11-18, starting from `f4ec111`)**:
  - README rewritten (it still said "design only"): what it is, installing the Release wheel by URL, a first run and a comparison with `backend/examples` (the comparison says honestly that n=3 is not significant), command table, exit codes, packs, development commands.
  - Release form (decided by the owner): no PyPI, keep using the GitHub Release wheel (§17).
  - Whole-repo /simplify (four reviews, reuse, simplification, efficiency and layering, about 46 items, and after de-duplication these were accepted):
    - runner: target calls and judge calls share one retry loop `_attempts` (the item E5a left for E7; the budget-stop and retries-exhausted hints are still kept apart); run status is down to one rule `_status(gen, judge_skipped)`; `_summary` is shared by run and rescore; with `--gate` the scorers are built only once; `ascore_outputs` dropped the write-a-placeholder-then-fill-in approach.
    - `Redactor.warnings` (three identical maskings merged into one); the scorers' `make_score` (six scorers each filled in the identity fields); the CLI's `_errors` decorator replaces ten try/except blocks (a LiteEvalError in any command exits by exit code, and none that were missed turn into a traceback); file hashing goes through `canonical.file_sha256`, and the comparison seed through `sha256_hex`; the `env:<NAME>` secret naming is in one place only, `redact.env_secrets`; `RunRecord.config_name` (a read-only property, not a new field, replacing five separate derivations with three different defaults); `window` is only in index; `views.comparison` dropped a parameter nobody passed; `baseline set` no longer reads outputs.jsonl.
    - Viewer efficiency: a run and its per-question results are cached by run.json's modification time (when looking at a case, every request used to re-read this run and all other runs of the same dataset; exporting the HTML of two runs used to re-read the other run for every case); `detail()` pre-groups the per-question outputs and scores (it used to scan the whole run for each question); the full load of the datasets page (including the corpus hash, which is several GB for LongMemEval M) is cached by file size and modification time. Each of the two caches has a test, and both mutations, removing the cache and ignoring the modification time, were seen to turn red first.
    - frontend: query keys and parameters are all in `data/queries.ts` (datasets, dataset cases and trends used to be inline; the keys invalidated after an annotation save are taken from there too); the `Loaded` component replaces ten "loading / error / data" ternaries; retrieval hit scores are formatted with `metric()`; the dataset query has a staleTime of 5 minutes and does not refetch on window focus. Also fixed a bug where `metric()` swallowed the minus sign (failing test first).
    - Not accepted (reasons noted): a scorer plan object `plan_scorers`; turning the Target's optional members into base-class defaults (a Python-factory Target is user code, and reading optional members with `getattr` is part of the protocol); a directory interface for the Viewer's selectors and a SQLite per-question table; SQL filtering in the index; running judging and generation as a parallel pipeline; running different setup groups in parallel (which ones are skipped when the budget runs out would depend on timing); putting the calibration confusion matrix into `RunSummary` (a contract field, to be added when the Viewer needs to display it); having the KB export's vendor / model / effort come from `info()` (the sibling projects would have to change too); hashing the corpus in parallel (correct, but the gain is only for huge corpora): all are larger redesigns whose gain does not justify the change.
  - Bugs fixed (each with a failing test first): when a paid judge was allowed in CI, the run record of a free Target still had `paid_allowed` false (§18.13 requires the allowance to be recorded truthfully); the key of the frontend dataset-cases query left out `limit` (found by Codex; today there is only one caller with a fixed limit, so it was a latent problem).
  - Codex (whole-repo read-only review): only the `limit` item above; failure counts and status, the output cache key, the Viewer cache, masking and HTML export, Viewer security and paths, statistics and gates, the CI billing guard and CLI exit codes were all checked and no problems were found.
  - Break script: of E7's 14 changes, 12 turned red in the first round; for the two that did not (the hint when the budget stops at the third retry, the order of scores.jsonl) tests were added and then 14/14 turned red, and the source was restored.
  - Verification: ruff, format, mypy; pytest 525 passed (locked versions, `CI=true`, lowest declared versions); `-m kb` passes; `openapi.json` is up to date; frontend typecheck, lint, test (19), build, e2e (8) pass.

## 17. Decided pre-start questions (2026-11-13)

Four questions put to the owner before work started. The first was decided by the owner, and the owner left the rest to the implementer to decide following the design's recommendation.

- **Public**: public on GitHub `ArthurGao/lite-eval`, licence Apache-2.0 (the same as llm-steward).
- **Run records**: the run that the baseline points to is committed to the user's repository, and the rest is gitignored. At `lite-eval baseline set` it prompts to commit that run directory.
- **E4's judge model**: only the local Ollama. Paid models are used only when the owner explicitly asks for it for one particular run, and the billing is declared per §18.13.
- **Release form**: no PyPI in v1. The frontend build output is in gitignore, so installing straight from git would lack the Viewer; therefore the CI packaging job (§18.14) publishes the wheel with the frontend
  as an attachment of a GitHub Release, and users install that wheel by version number. Installing from git by commit alone also works, but only the API and CLI can be used, and `lite-eval view`
  gives the "build the frontend first" error of §18.14. E7 will decide whether to publish to PyPI.
  Decided at E7 (owner, 2026-11-18): no PyPI, keep using the wheel attached to GitHub Releases; the three sibling projects all install this wheel by URL, and there is no other user at present.

## 18. Specification details (go by this when implementing)

The sections above say what to build and why; this section pins down the places that are easy to read two ways. When two statements disagree, this section wins (and the earlier statement is corrected).

### 18.1 Repeated runs

- `repeat` starts at 0, is written on every Output and Score, and is also part of the cache key.
- **Value per case**: for one scorer, take the mean of the `value` of all repeats of that case. A repeat that errored counts as a failure (`value=0`, `passed=false`), not as missing.
- **Run-level metric**: compute each case's value first, then average over all cases. Ranking metrics (hit@k, MRR) are computed for each repeat separately first, then averaged over cases.
- **Spread**: compute the run-level metric once for each repeat, and report the minimum, maximum and mean.
- **Different numbers of repeats in a comparison**: pair using each case's mean, and then always use the bootstrap (§18.2), not McNemar.
- **Spread warning**: of the two runs, if either side's run-level metric spread (maximum − minimum) is greater than 0 and greater than or equal to the difference of the two means, give a `within_noise` warning. No warning when the spread is 0: the spread of a single-run run is always 0, and a warning on a tie with the baseline carries no information (decided after discussion in the E2 Codex review).

### 18.2 Significance tests

Use only cases that are present on both sides and comparable (§18.3). An errored case is a failure, not missing.

- **McNemar**: used when both sides ran only once (`repeats=1`) and the metric is binary.
  - Counts: `b` = number of cases the baseline passed and the candidate failed; `c` = number of cases the baseline failed and the candidate passed.
  - Exact binomial test, two-sided, no continuity correction: `p = min(1, 2 · BinomCDF(min(b, c); b + c, 0.5))`; when `b + c = 0`, `p = 1`.
  - `p < 0.05` counts as significant.
- **Paired bootstrap**: used in every other case.
  - The resampling unit is the case: draw n cases with replacement, taking the same cases on both sides; `B = 10000`.
  - Statistic = the candidate's metric mean − the baseline's metric mean.
  - The interval is the 95% percentile interval; significant if the interval excludes 0.
  - Seed = `int(sha256(baseline_run_id + "|" + candidate_run_id + "|" + metric)[:16], 16)`, which makes it reproducible.
- The report always states n, the number of discordant cases, and the p value (or interval).

### 18.3 Comparability

- **Different dataset name**: cannot compare, an error (exit code 2).
- **Different dataset hash**: align by case id, and compare each case's own hash (sha256 of canonical JSON).
  - Compare only the cases with identical content;
  - list the changed, added and deleted cases separately;
  - mark the result `partial_overlap`.
- **Different scorers**: when the scorer name or version of the same metric differs, the metric is marked `incomparable` and no difference is shown.
- **Rules for the gate**:
  - when a gate refers to an `incomparable` metric, it ends with exit code 2 (config error) and cannot quietly pass;
  - a `partial_overlap` comparison cannot be used for a relative gate by default, unless the Suite writes `gate.allow_partial_overlap: true`, and the report says so.
- **Duplicate case ids**: an error right at dataset load.

### 18.4 Exit codes and priority

Decide in the order below and stop at the first match:

1. **2 config or input error**: the Suite or dataset is invalid, the comparison is not comparable (§18.3), or the CI guard refuses to run (§18.13). All of these are checked before calling the Target.
2. **3 run failed**: the runner itself crashed, or not one case got a score (for example the budget ran out before it started).
3. **1 gate failed**: any gate condition is not met.
   - A case that errored counts as a failure and pulls the metric down, but does not by itself cause exit code 3.
   - A run with `status=partial` counts as a gate failure by default, unless `gate.allow_partial: true` is written.
4. **0**: everything else.

In pytest, `assert_gate` correspondingly raises `ConfigError`, `RunError`, `GateFailed` (which inherits `AssertionError`).

### 18.5 Cache

- **The Target declares two kinds of information separately**:
  - `versions()`: versions that affect the output, such as the engine version, the model id and the exact snapshot it maps to, the prompt version; **part of the cache key**.
  - `info()`: record only, such as the git sha and hostname; not part of the cache key.
- **Cache key** = `sha256(canonical({"target": name, "config": config(), "versions": versions(), "setup": setup_identity(), "dataset": dataset_hash, "case": case_hash, "repeat": repeat}))`.
  - The dataset hash includes the corpus (§18.6), so when the corpus `setup` uses changes, the cache of every case becomes invalid. Better to recompute too much than to pass off an old output as a new result.
- **Versions of HTTP and subprocess Targets**: write `versions: {…}` in the Suite, which is a static declaration. Alternatively write `versions_from`, one of the two:
  - `versions_from: {url: …, path: "$.versions"}`: requested once at setup;
  - `versions_from: {command: [...]}`: run once at setup, reading JSON from stdout.
  - When neither is written, this Target's cache is off by default (`cache: off`) and an `unversioned_target` warning is given; only an explicit `cache: on` in the Suite turns it on.
  - After it is turned on explicitly, the `unversioned_target` warning is still written into the `warnings` of every run: when the code under test changes but the URL or command does not, the cache returns old outputs, and this risk must stay visible.
- **Storage**: each cache entry stores one masked Output (§18.12), at `.lite-eval/cache/<first two characters of the key>/<key>.json`. The run record copies the Output and does not refer to the cache.
- **Reading**: a corrupt cache entry is treated as a miss and a warning is given.

### 18.6 Canonical JSON and hashes

- **Canonical JSON**:
  - every string is first Unicode NFC-normalised;
  - then `json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)`, encoded as UTF-8.
- **Case hash** = sha256 of the canonical JSON, covering `{id, input, expected, tags}` (without `provenance`).
- **Dataset hash** = sha256 fed in this order:
  1. the byte string `b"lite-eval-dataset-v1\n"`;
  2. the canonical JSON of the three fields `name`, `version`, `pack` in `dataset.yaml`;
  3. the hash of each case, sorted by id;
  4. for each file in the corpus directory, sorted by POSIX relative path, feed `path + "\0" + sha256(file bytes)` in turn.
- Symbolic links are not allowed in the corpus, and meeting one is an error; metadata such as modification time does not take part in the hash.

### 18.7 run_id and ordering

- **Format**: `run_id = <UTC time, to the microsecond, YYYYMMDDTHHMMSSffffffZ>-<config name slug>-<6 random hex characters>`.
- **Ordering**: always by `(UTC microseconds of started_at, run_id)`. The order within the same microsecond has no meaning and only keeps the order stable; it does not promise to reflect the real time order.

### 18.8 Recording failures

- **Target error**: raise `TargetError(kind, message, usage=…, latency_ms=…)`, and the runner writes the known usage and time into the Output.
  - Any other uncaught exception is recorded as `exception`, with empty usage.
  - A truncated output (for example `finish_reason=length`) is recorded as `truncated`, and `value` still keeps the part already generated.
- **Scorer or judge error**: the Score's `error` has a value, with `value=0` and `passed=false`.
- **Summary**: `target_errors` and `scoring_errors` are counted separately; the denominator is always all cases × repeats.

### 18.9 Annotation

- **Fields**:

  ```
  Annotation: {schema_version, annotation_id (uuid7), dataset, case_id, case_hash, run_id | null,
               category: real_miss | label_wrong | excluded | unclear, note (≤ 4 KB), author, created_at,
               supersedes: annotation_id | null}
  ```

- **Append-only**: a correction writes a new record, with `supersedes` pointing to the old one.
- **The triage currently in effect**: for a given `(dataset, case_id, case_hash)`, the one that no record supersedes.
  - On write, while holding the file lock, it must be confirmed that the record `supersedes` points to is still in effect; if not, the write is refused (HTTP 409, and the CLI prompts to reload), so a forked correction chain cannot appear.
  - On creating a new one (`supersedes` empty), if this key already has an effective record, the write is also refused, and a correction of that record is required instead.
  - So each key has at most one effective record at any time, and there is no need to order by timestamp.
- **When the case content changes** (`case_hash` differs): the old triage is not carried over to the new content automatically, and the Viewer shows it as "triage of an older version".
- **The dataset is not changed**: really fixing a label means changing the dataset file in git, so the hash changes (R9). Annotation needs no version number.
- **Writing**: append one line to `annotations.jsonl`, holding an exclusive file lock before writing (`fcntl.flock`, lock file `annotations.jsonl.lock`), and `fsync` after writing. The Viewer and `lite-eval triage` use the same lock.
- **Addendum (decided in E3)**:
  - The limit of `note` is 4096 bytes of UTF-8, not 4096 characters (one Chinese character is three bytes). No annotation had been written before E3, so this tightening does not affect old records.
  - `author` is taken in turn from `LITE_EVAL_AUTHOR`, `git config user.name`, the login name; the Viewer's request carries no author.
  - `annotation_id` is a hand-written UUIDv7 (Python 3.12 has no `uuid.uuid7`).
  - A correction must have the same key as the one it corrects, or it is refused.
  - The definition of **miss**: the case has an output that errored, has a repeat that produced no output (a partial run, with the error recorded as `not_generated`), or has a scorer that gave `passed=false` or a scoring error. The per-case score uses the same definition as the run summary (§18.1: a missing repeat counts as 0), computed by `analysis.summary.case_results`. The miss triage page and `triage` list only misses.

### 18.10 Viewer security

- Listen only on loopback addresses; the request's `Host` must be loopback (against DNS rebinding).
- **Write requests** (only annotation) must meet three conditions:
  - `Origin` must be the Viewer's own loopback address;
  - the request header carries `X-Lite-Eval-Token`: a token generated randomly each time the process starts, injected into the page, to guard against CSRF;
  - the request body is validated by pydantic, with a size limit of 16 KB.
- Read endpoints need no token, but `Host` is still checked.
- **No CORS headers at all** (no CORS middleware is installed, and the `OPTIONS` preflight is not allowed): the browser's same-origin policy therefore keeps other websites from reading the Viewer's responses. A test confirms there is no `Access-Control-Allow-*` in the responses.
- Other processes on the same machine can read the token, which is acceptable within the threat model (the Viewer is for its owner only).
- See llm-gateway's `server/guard.py`.

### 18.11 HTTP and subprocess Targets

**The Suite is trusted local config**: like code, it can run commands and reach the network. Do not load a Suite from an untrusted source.
At the start, the runner prints the command or URL it is about to run.

**HTTP Target**

- **Config items**:
  - `method` (default POST), `url`;
  - `headers`: a value may be written as `${ENV_NAME}`, taken from the environment at run time, and never written into logs or records;
  - `body`: a Jinja2 template, rendered with `SandboxedEnvironment`, with `case` as the variable;
  - `output` and an optional `trace`: JSONPath, supporting the subset of RFC 9535 `$.a.b`, `$.a[0]`, `$.a[*].b`;
  - `timeout_s`; `max_response_bytes`, default 10 MB.
- **Response handling**:
  - 2xx: parse as JSON and take values with JSONPath;
  - any other status code: `error.kind=http_status`;
  - not JSON, or the JSONPath finds no value: `invalid_output`;
  - over the size limit: `truncated`;
  - transport-layer error: retry per §6.

**Subprocess Target**

- **Start**: started in `setup` from an argv list, not through a shell. Config items are `cwd` and an environment-variable allowlist `env_allow`.
- **Ready**: after the process starts, it must print one line `{"ready": true}` within `startup_timeout_s`.
- **Each case**: write one line `{"case": …, "repeat": n}` to stdin, and read one line `{"case_id": …, "repeat": n, "output": …, "trace": …}` from stdout within the timeout; a line longer than `max_line_bytes` (default 10 MB) is recorded as `truncated`.
- **Serial**: a process handles only one case at a time, and the runner uses a lock so that no other case slips in between writing a line and reading a line; `concurrency` has no effect on a subprocess Target. A reply whose `case_id` or `repeat` does not match the current case is recorded as `invalid_output`.
- **After a timeout**: the process may still hold a late reply, so after a timed-out case is recorded as `timeout`, the process is restarted before continuing. This kind of restart does not count toward the "restart only once when the process exits midway" below.
- **stderr**: written, masked, to `target.stderr.log` in the run directory, with a size limit of 5 MB.
- **Process exits midway**: the current case is recorded as `exception` and the process is restarted once; if it exits again, all remaining cases are recorded as `exception` and the run is marked `partial`.
- **End**: at `teardown`, close stdin, wait 5 seconds, then `terminate`, and finally `kill`.

### 18.12 Masking

- **Scope**: every string that is written to disk or displayed:
  - the Output's `value`;
  - the `input`, `output` and `attrs` of every span in the trace;
  - error messages;
  - the Score's `reason` and `evidence`;
  - `JudgeRecord.raw`;
  - cache entries, `target.stderr.log`, and all exports.
- **When**: the runner does it once before writing the cache and the run record; the server and exports do it again before output.
- **Which rules an export uses**: the `redact` recorded in the run's `run.json`, plus rules appended by the export command's `--redact`; rules can only be added, not removed. A comparison export uses the union of the two runs' rules.
- **Escaping of embedded data**: HTML export puts the data into `<script type="application/json">`, and after serialising converts `<`, `>`, `&`, U+2028, U+2029 to `\uXXXX`, so a `</script>` in case content cannot end the tag.
- **Rules**:
  - built-in `connection_strings`: connection strings in both URL form and `key=value` form are parsed by libpq's rules, and any parameter whose name contains `password` is masked (following lite-mem's approach);
  - built-in `api_keys`: `sk-…`, `stw_…`, `Bearer …`, `AKIA…`, and values after `api[_-]?key`, `token`, `secret` followed by `=` or `:`;
  - built-in `emails`;
  - the Suite's custom regexes.
  - Replaced with `«redacted:<rule name>»`.
  - **Known values** (E4d): a Target declares the secrets it uses with the optional `secrets() -> {name: value}` (the `${ENV}` headers the HTTP Target resolved, the environment variables the subprocess Target let through), read after setup. The runner builds, for each config group, a redactor holding these values and uses it in generation (including before writing the cache), scoring, artifacts and run warnings, replacing by value first and then running the rules above:
    - Forms: as is; each character that is not ASCII alphanumeric may be as is, JSON-escaped (`\"`, `\/`, `\uXXXX`, either case accepted, with surrogate pairs outside the BMP) or HTML-escaped (named, decimal and hexadecimal entities, with at most 8 leading zeros: encoders do not write more, and this gives each character's longest form an upper bound, so there is a basis for how much to trim when truncating), freely mixed (encoders differ: Go writes `<` as `<`; the security review of commit `9d8e815`); `quote` and `quote_plus` (either case of the `%XX` hex accepted). Replaced with `«redacted:<name>»`, and `env:NAME` for HTTP and subprocess; longer values are replaced first; each form is one scan, and there is no nested repetition in the regex, which keeps it linear.
    - JSON keys are normally kept, but a part that equals a known secret is replaced too.
    - On replacement, both the markers already in the text and the markers inserted this time are first swapped for a private-use character (from U+F0000) to protect them, and swapped back at the end: a shorter secret (such as `env`) does not rewrite a marker such as `«redacted:env:TOKEN»`, and masking twice gives the same result (Codex E4d).
    - The Target config in the record (`TargetRecord.config`) and the `describe()` printed before the run are also masked by known values; the cache key uses the original value and is unaffected (Codex E4d).
    - `rescore` reads an existing run directory and uses the rules recorded (known values are only names): if a run from before E4d left a secret in its output, that secret was already in that directory, and `rescore` adds no new exposure; records from E4d on were masked before being written (Codex proposed that `rescore` refuse or re-fetch the values; not accepted).
    - An empty value is skipped; one shorter than 8 characters is still masked, but the run gives a `short_secret` warning (it may mask ordinary text by mistake), and the warning contains only the name.
    - `RedactConfig.known` records only names and never values; `rescore` and export work on content that is already masked and do not need these values.
    - For text that will be truncated, quoted or handed to a library to summarise, the Target masks by known values itself first after getting the raw text (and the runner masks again later): a truncated secret cannot be recognised.
    - Error messages when setup and teardown fail (the CLI prints them) are also masked by this Target's known values; when there are known values the original exception chain is not kept, or pytest would print the raw text in the traceback.
  - Masking must take linear time: the system under test's output is not under our control, and a quadratic rule would stall on adversarial long text (measured 287 ms at 16k characters, estimated more than ten minutes at 1 MB).
    - Every part that could start matching from many positions has a length limit: URL scheme ≤ 32, user name ≤ 256, host name ≤ 255; email local part ≤ 64, domain ≤ 255; parameter name ≤ 64.
    - The password in a URL has **no** length limit, and may contain an unencoded `@` or `/`: first find all `scheme://user:` start points and all valid `@host` positions, and for each start point use binary search for the first `@host` after it in the same whitespace-free token.
    - Tests confirm that several kinds of adversarial text of 100 KB all finish within 2 seconds, and a 1500-character password is replaced as a whole.
- **Datasets are not masked**: a dataset should not hold sensitive content in the first place (R20). A scan runs at load time, and a suspected secret makes it an error.
  - A false positive is exempted match by match: write `secret_scan_allow: [{case_id, rule, match_sha256, reason}]` in `dataset.yaml`, where `match_sha256` is the sha256 of the matched text and `reason` is required. There is no way to exempt a whole dataset at once.
  - Exemptions that were used are written into `run.json`'s `warnings`; an exemption that is listed but matched nothing also gives a warning, to remind you to remove it.
  - **Only `cases.jsonl` is scanned, not `corpus/`** (stated in E4b, the owner left it to me on 2026-11-14): the corpus is material the Target reads and does not enter run records; what leaves the Target, such as output, trace, errors and artifacts, is handled by the suite's masking rules. The corpus of a public benchmark (the ShareGPT chats in LongMemEval) would give false positives and its original text cannot be changed, so exempting match by match is pointless. A corpus we write ourselves should not contain secrets either (R20), and that relies on whoever writes the data, not on scanning.
- **Tests**: at least one test case per form, stating which parts should stay readable.

### 18.13 Billing guard in CI

- **Every place that calls a model must declare its billing**: `billing: free | subscription | per_token`.
  - Judges: written in the role mapping;
  - Python Target: the `billing()` method;
  - HTTP and subprocess Targets: written in the Suite.
- **Not declared**: a judge is always treated as `per_token` (strict rather than lenient); a Target is recorded as `unknown`.
- **In CI mode** (`--ci` or `CI=true`): if any one place is not `free`, the run is refused with exit code 2. An exception needs two conditions to hold at the same time:
  - the environment variable `LITE_EVAL_ALLOW_PAID=1`;
  - the Suite writes `allow_paid_in_ci: true`.
  - The fact that it was allowed is written into `run.json`.
- **Local default**: port 11434 on loopback (Ollama) counts as `free` by default; an llm-gateway profile must declare its billing explicitly.

### 18.14 Packaging the frontend

- `backend/scripts/build_frontend.py`: runs `pnpm -C ../frontend install --frozen-lockfile && pnpm -C ../frontend build`, and copies `frontend/dist/` to `backend/src/lite_eval/server/static/`.
- **Putting it in the package**: use hatchling's `artifacts` setting so that `src/lite_eval/server/static/**` goes into both the wheel and the sdist even though it is in gitignore.
- **CI's packaging job**:
  1. build the frontend;
  2. `uv build`;
  3. install the wheel in a clean venv;
  4. run `lite-eval view --check`: start on a temporary port, request `/` and `/api/health` and both return 200, then exit.
- **Release** (§17): when a `v*` tag is pushed, after the CI packaging job passes the four steps above, the wheel and sdist are uploaded as attachments of that tag's GitHub Release.
- **When there is no frontend build output**: `lite-eval view` reports a clear error (hinting to build the frontend first), and the API can still be used on its own.

### 18.15 SQLite index

- **Only one writer at a time**: at startup the Viewer takes an exclusive lock on `.lite-eval/index.lock`; a second Viewer using the same directory refuses to start.
- **Version**: the index has `meta(schema_version)`. When the version does not match, the whole index is rebuilt: built into a temporary file first, then renamed atomically.
- **Incremental update**: every 5 seconds (or when a request comes in) scan the modification time and size of `runs/*/*/run.json`:
  - newly appeared: added to the index;
  - changed: re-indexed;
  - gone: removed from the index.
- **The runner's write order**: first write `outputs.jsonl` and `scores.jsonl`, and finally write `run.json` with "write a temporary file + `os.replace`". A directory without `run.json` means it is still running, and the index skips it.

### 18.16 Numbers that must stay the same when migrating

Each migration is verified in two layers:

1. **Scorer equivalence**: in a unit test, feed the same batch of hit results to the original function and to the new scorer, and the outputs must be the same.
2. **End-to-end equivalence**: with the same built index or data, in the same process, run the original script and lite-eval once each. Latency is not compared.

| Project | Original definition (must be ported as is) | Outputs that must be the same |
| --- | --- | --- |
| lite-rag | See below | Each question's `answer_rank` and `doc_rank`; overall and per-`kind` `hit@1`, `hit@5`, `mrr@10`, `doc_hit@5`, with error ≤ 1e-9 |
| llm-steward | See below | Each query's recall value, the mean, the miss list; both with rerank and without |
| lite-mem | See below | Each question's `hop1`, `hop2`; the summary counts of `hop1`, `hop2`, `both` |

- **lite-rag** (`judge`, `metrics`, `_norm`, `_nearest_rank` in `src/lite_rag/eval.py`):
  - For each question take the top 10 retrieved and scan from the top: the first hit from the target document is recorded as `doc_rank`; the first hit from the target document whose normalised text contains the normalised evidence sentence is recorded as `answer_rank`.
  - The steps of text normalisation (`_norm`):
    1. remove Markdown heading marks, `**`, `__` and escape characters;
    2. merge runs of whitespace into a single space;
    3. remove whitespace between two CJK characters.
  - `hit@k` = the share with `answer_rank ≤ k`; `mrr@10` = Σ(1/answer_rank) / n; `doc_hit@5` is computed with `doc_rank`.
  - `p50` = `statistics.median`; `p95` = nearest-rank: after sorting take the `ceil(0.95·n)`-th value.
  - Grouped by `kind`.
- **llm-steward** (`_recall` in `backend/tests/eval/test_recall.py`; corrected to the code after the E4 investigation):
  - The service is called inside the test process with `TestClient` (`create_app` + local fastembed MiniLM, no LLM calls), and after the corpus is written through the HTTP API, `drain()` is called; SQLite and Postgres are each run once.
  - Each query does `POST /v1/retrieve` with `top_k=8`, `include_pinned=False`;
  - recall = |expected keys ∩ returned keys| / |expected keys|, that is `recall@8` in relevant-set mode;
  - overall = the mean of all queries' recall; queries with recall < 1 go into the miss list;
  - gate threshold 0.92, only for the pass without rerank (baseline 0.960); the pass with rerank (0.980) is only reported;
  - when `degraded` is not empty the run is treated as an error: after migration that case is recorded as `invalid_output`, and the test additionally asserts that no case in the run errored, because otherwise a case scoring 0 could still let the mean pass the line;
  - not in CI (`-m eval`), and the same after migration.
- **lite-mem** (`_has` and `main` in `eval/multihop.py`; corrected to the code after the E4 investigation):
  - Data `eval/multihop.json`: 2 fictional users (one English, one Chinese), 30 memories and 10 questions each, 20 questions in all; each question has `q`, `answer`, `hops`, and there is no grouping by type.
  - A new SQLite database each time, memories written directly with `infer=False` and no model call; the embedding model is bge-m3 on Ollama.
  - For each question the top `top_k` are retrieved (default 5), and `follow_up` is one parameter of the whole run, not set per question; the baseline result `eval/results/2026-11-12-s10-multihop.json` has 7 (top_k, follow_up) groups.
  - A hop counts as a hit when: there exists a memory for which `k.lower() in m.lower()` holds for all the keywords of that hop (not casefold, whitespace not merged); this corresponds to the memory pack's `hop<n>` (§18.23).
  - The summary counts `hop1`, `hop2`, `both`; `both` corresponds to `all_hops`.
  - End to end needs bge-m3 on Ollama and sits under an opt-in marker; the scorer-equivalence test needs no model.

### 18.17 Definition of the self-checks

- **`identical_scores`**:
  - Condition: under the same Suite and the same dataset hash, two runs have different `target.config` but exactly the same per-case scores for all scorers.
  - Action: add this warning to both runs.
  - Scope: compare only the configs within **one `lite-eval run` call**. A run record is immutable once written, and a warning cannot be added to an old run afterwards (decided in E1).
- **`feature_not_exercised`**:
  - A Suite can declare `expect_features: [merge, …]`;
  - the Target indicates which feature was actually triggered with a `kind="decision"` span in the trace plus `attrs.feature`;
  - at the end of the run, if a declared feature appears in the trace of no case, a warning is given.
- **Cross-check of ranking metrics**:
  - in tests, the self-written hit@k, MRR and nDCG must agree with ranx on a fixed sample run;
  - `lite-eval run --crosscheck` computes again with ranx at run time and gives a warning if the difference exceeds 1e-9.
- **`within_noise`**: defined in §18.1.
- **Others**:
  - `all_failed`: there are outputs, but not one score passed or is greater than 0.
  - `target_errors`: warns when errored outputs (including timeouts, call exceptions and 429s when the quota runs out) are 10% or more of all outputs, stating the number of errors, the counts by error kind and one example; the terminal report's header also states the number of errors. Errors still count in the denominator (rule 4), and this warning keeps people from taking such a score as the level of the system under test (2026-11-18: gpt-sub's subscription quota ran out midway through a calibration, 104/223 calls failed, and the run was still complete with a score of 0.529, yet the error count could only be seen by turning to the end of the report).
  - `empty_group`: the Suite declares the groups it expects with `expect_tags: {kind: [fact, multihop]}`, and a warning is given when some value has no cases (groups come from tags, and without a declaration there is no way to know "should be there but is not").
  - `synthetic_dataset`: `dataset.yaml` has `synthetic: true`, or some case's `provenance.synthetic` is true.
  - `unversioned_target`: `versions()` is empty, so a change of engine or model would not be noticed by the cache.
  - `budget_exhausted`, `cache_corrupt`, `secret_scan_allowed`, `secret_scan_allow_unused`: see §6, §18.5, §18.12.

### 18.18 Setup sharing and warm-up

- **Grouping**: the runner groups configs by `(target name, canonical(setup_identity()))` and runs one group at a time; within a group, configs run in the Suite's order, and the order between groups does not affect results.
- **Lifecycle**:
  1. the first config in the group (the leader) calls `setup(dataset)`;
  2. the other configs (followers), if they implement `share_setup_from(leader)`, call it to take over the leader's setup; those that do not implement it each call `setup(dataset)` and give a `setup_not_shared` warning (the result is still correct, only slow);
  3. after each config finishes its own cases, the follower is torn down first; after the whole group has run, the leader is torn down last.
  4. Every Target attempts its teardown, and one failure does not affect the later ones. When the run has already errored, a teardown failure is attached to the original error as a note and does not replace it; when the run has not errored, a teardown failure is reported as `RunError` (exit code 3).
- **Read-only**: a shared setup is read-only during `run`. A Target that changes shared state (for example a memory system that writes in `run`) cannot be shared: `setup_identity()` must include the config name, so that it forms a group of its own.
- **Warm-up**: after setup and before timing, each config calls `run` once on the first case sorted by id and discards the result: it enters neither the cache nor `outputs.jsonl` and is not counted in latency; the number of calls and the cost still count into the budget and `cost` (L6), and when the warm-up raises a `TargetError` carrying usage, that usage counts too. When all cases hit the cache and the Target does not need to be called, no warm-up is done.

### 18.19 Gate semantics

- **The value of a metric**: the run-level metric (§18.1: first compute each case's mean over all repeats, then average over cases). Errored cases count as failures.
- **Absolute threshold**: `{metric, min}` requires the value ≥ `min`; `{metric, max}` requires the value ≤ `max` (for metrics where smaller is better, such as latency and cost). When both are written, both must be met.
- **Relative threshold**: `{compare_to: baseline, max_drop, require_significant}`, using only comparable cases (§18.3).
  - `delta` = candidate − baseline (for metrics where smaller is better the sign is flipped, so it is uniformly "larger is better").
  - `require_significant: false` (default): `delta < −max_drop` fails.
  - `require_significant: true`: fails only if `delta < −max_drop` **and** the drop is significant per §18.2. This keeps noise on a small sample from blocking a merge; the report still lists drops that are not significant.
- **Repeated runs**: only the mean is looked at, not a single repeat; the `within_noise` warning (§18.1) is only reported and does not change the gate's conclusion.

### 18.20 Integrating Suite and Python Target (decided in E1)

- **Eval root directory**:
  - When the Suite is at `<root>/suites/x.yaml`, the root is `<root>`; otherwise it is the directory the Suite file is in. It can be overridden with `--root` or `LITE_EVAL_ROOT`.
  - Run directories are at `<root>/runs/<suite>/<run_id>/`, and the cache is at `<root>/.lite-eval/cache/`.
  - A relative path in the Suite's `dataset:` is resolved against the directory of the Suite file.
  - `suite.path` in `run.json` records a path relative to the root (with `..` when the Suite is outside the root) and never writes this machine's absolute path; `rescore` uses it to find the Suite again.
- **Python Target**:
  - Form: `python: module:factory`. Each item of `configs:` is a dict, `name` is required (only lowercase letters, digits, `.`, `_`, `-`, since it goes into run_id and the directory name), and the other keys are passed as keyword arguments to `factory(**params)`.
  - The module is imported from one level above the root (the user's repository root), and is put on `sys.path` only during the import.
  - The return value must satisfy the protocol of §5.3; an optional attribute `deterministic: bool` (default True) makes the Suite run 2 times when it is False and the Suite does not write `repeats`.
  - Which repeat is current: before each call of `run(case)` the runner sets the context variable `lite_eval.targets.base.current_repeat` (starting at 0), which a Target that wants to seed by repeat reads; the signature of `run` does not change.
- **Suite fields (E1)**:
  - Fields: `schema_version, name, pack?, dataset, targets[{python, configs}], scorers[], metrics[], repeats?, concurrency, timeout_s, seed, budget{max_cost_usd, max_calls}, redact[], expect_features[], expect_tags{}, allow_paid_in_ci, cache (auto|on|off), gate`.
  - An unknown field is an error (exit code 2).
  - `gate` is recorded as is into `run.json` and is not enforced until E2.
  - When `pack` is not written, `scorers` must be. E1 has only the cost pack, which comes with every run.
- **How scorers are written**:
  - A string, for example `exact_match`; or a dict with parameters, for example `{name: contains, case_sensitive: false, field: answer}`; combinations are written `{all_of: […]}`, `{any_of: […]}`, `{weighted: [{scorer: …, weight: 2}, …]}`.
  - Canonical form: with no parameters it is the name itself, with parameters it is `{name: params}`, a combination is `{op: [canonical forms of the sub-scorers]}`, `{weighted: [{scorer, weight}]}` (weight recorded as a float). The Suite form `{name: …}` and the record form `{name: params}` are both normalised first, so the same scorer has the same id however it is written (E2 pairs by id).
  - The scorer name in the record is the canonical JSON of the canonical form (the name itself when there are no parameters). `rescore` uses it to rebuild the scorer.
  - E1's deterministic scorers: `exact_match`, `contains`, `regex`, `keywords`, `must_not`, `set_recall`, `set_precision`. Common parameters: `field` (a path in the output), `expected` (a path in `case.expected`), `case_sensitive`.
  - `all_of`: the value is the minimum, and it passes only if all pass; `any_of`: the value is the maximum, and it passes if any passes; `weighted`: weighted average, with `passed` empty. Each sub-scorer's result goes into the combined score's `evidence`.
- **Runner details**:
  - An errored output is not written to the cache and is regenerated next time; timeouts and occasional errors are not fixed in place by the cache.
  - Every output (cache hits included) is masked by the **current** Suite's rules before being written into the run record: after a rule is added, content in an old cache entry does not leak as it is; when masking changed the content, the cache entry is replaced as well.
  - Scoring uses the masked output, so `rescore` (which reads the already masked output in the run directory) and the first scoring give the same result.
  - Budget: one budget is shared by the whole Suite (all configs of one `run` call). `max_calls` counts each call (retries included) and is exact under concurrency too (there is no `await` between checking and counting); `max_cost_usd` stops after spending **exceeds** the limit, so `max_cost_usd: 0` still allows free calls, and under concurrency calls already sent may push spending slightly over the limit.
  - After a transport error, if the budget does not allow another try, there is no waiting and no retry, and this failure is still recorded as an `exception` output and counted in the denominator.
  - A config with no output at all is recorded as `status=failed`, the run directory is still written, and the exit code is 3.
  - Configs run one after another in order of name, and the cases within a config run concurrently.
- **`rescore`**:
  - Reads outputs from the run directory, and finds the Suite and dataset again from `suite.path`. If the dataset hash has changed it refuses (exit code 2), and the outputs should be regenerated.
  - When reading a run directory, the same `(case_id, repeat)` appearing twice is refused (exit code 2): a record that has been tampered with must not quietly produce a wrong summary.
  - The new run's `cost.calls` is 0, `cache` is zeroed, and `rescored_from` points to the original run.
  - `--scorer` can be given more than once, written as a name or JSON (either the Suite form or the record form).

### 18.21 Integrating the retrieval pack, comparison and gate (decided in E2)

- **Retrieval scorers**:
  - Output convention: `value` (or the part `field` points to) is a hit list `[{doc, text, id?}]` sorted by rank.
  - `expected` has two forms: evidence-sentence mode `{doc, evidence}`, whose judgement is word for word the same as lite-rag's `judge` and `_norm` (§18.16); and relevant-set mode `{relevant: [ids]}`, where a hit's id is `id`, or `doc` when there is none, and a duplicate id counts only at its first appearance, as ranx sees it.
  - A scorer is named `<metric>@<k>`: `hit`, `doc_hit`, `mrr`, `ndcg`, `recall`. Each case's score is the value of that metric on that question, so the run-level metric is the mean over questions, and summary, grouping and comparison all go through the generic path. In evidence-sentence mode there is only one relevant item, so `recall@k` equals `hit@k`.
  - The retrieval pack defaults to `hit@1, hit@3, hit@5, mrr@10, doc_hit@5`; relevant-set mode usually adds `ndcg@10, recall@10`.
- **A scorer declares `verdict`** (written into `ScorerRef.verdict`, a field added in E2):
  - `value`: the value is 1 if and only if it passes (`exact_match`, `contains`, `regex`, `must_not`, `hit@k`, `doc_hit@k`, and `all_of` / `any_of` whose sub-scorers are all `value`). There is no separate `:pass_rate`, since it would only repeat the value.
  - `separate`: the value is a proportion, and pass or fail is separate (`keywords`, `set_recall`, `set_precision`, other `all_of` / `any_of`). It has a `:pass_rate` metric.
  - `none`: no pass or fail (`mrr@k`, `ndcg@k`, `recall@k`, `weighted`).
  - Whether there is a `:pass_rate`, whether the comparison uses McNemar or the bootstrap, and whether a per-case change is called "regressed / fixed" or "changed" all depend only on this declaration, and are not guessed from the values a particular run happened to get: when a continuous metric on a small dataset happens to be all 0 or 1, guessing from the values picks the wrong test (found in the E2 review). Records from before E2 have no such field, and only they fall back to looking at the values.
- **Comparison**:
  - `RunRecord.case_hashes` records each case's hash (a field added in E2; E1 records do not have it, cannot take part in a comparison, and must be regenerated).
  - McNemar is computed exactly with integers (no scipy dependency); the bootstrap resamples the per-case differences, using numpy's `default_rng(seed)`, drawing 1000 rows at a time (the same order of random numbers as drawing 10000 rows at once, with only a tenth of the memory), and the interval uses `numpy.percentile`'s default (linear interpolation) percentile. numpy is a core dependency.
  - Per-case changes: all cases that got worse come first (the largest drop first), then those that improved.
- **Gate**:
  - The Suite's `gate` can be written as a list of rules (the form of §8.4), or as `{rules: [...], allow_partial, allow_partial_overlap}`.
  - A rule is one of two kinds: `min` / `max`, or `compare_to: baseline` plus `max_drop` (and optionally `require_significant`). `latency_p50`, `latency_p95`, `cost_usd` come from the cost summary, smaller is better, and use `max`; writing a relative rule on them is an error at Suite load (otherwise it would be found only after the run had spent the money).
  - Run metrics (E4d): `errors` (the number of errored outputs), `error_rate` (`errors / outputs_expected`), `scoring_errors`, taken from the run summary, and like the cost metrics they can only be written with `max` / `min`. To require "no errored cases", write `{metric: errors, max: 0}`, and no separate assertion outside the gate is needed.
  - A rule can write `configs: [name…]` to judge only those configs (E4d: llm-steward puts only the pass without rerank through the gate). A name that is not in the Suite is an error at load (also without `--gate`). `RunRecord.gate` is still all the rules the Suite declares, with each rule stating which configs it covers; for a run to which no rule applies, the gate output says "no rule applies to config X" and not "pass" (`GateOutcome.applied`), and it does not count as a failure. But when **not one** of the runs handed to the gate was judged (for example `--config` ran only configs the rules do not cover), it is a config error: `gate_results` / `assert_gate` raise ConfigError and the CLI exits with code 2 (Codex E4d).
  - `RunRecord.gate` records a typed `GateSpec` (it is `null` in E1 records, which can still be read).
  - Whether a gate can be used (whether the metric exists, baseline, dataset, `partial_overlap`, scorer versions) is decided by one function, `analysis.gate.gate_problems`: it is called once before the run with what the Suite declares, and once after the run with what is in the record, so the two sides cannot disagree.
  - All threshold comparisons leave a tolerance of 1e-9: when the drop is exactly `max_drop`, 0.95 − 1.0 is −0.050000000000000044 in floating point, and it must not fail because of that.
  - `--gate` first does the config checks (the gate does not exist, the metric is not given by a scorer, no baseline, dataset names differ, `partial_overlap` not allowed, scorer versions differ), all before calling the Target, with exit code 2; then it runs, and exit code 3 if some run scored not one case; finally it judges the gate, and a failure gives exit code 1.
  - `lite-eval baseline set <run>` writes `<root>/baselines.yaml` (suite name → run_id), and prompts to commit the run directory together with this file (§17).
- **Setup sharing**: a Target can implement `share_setup_from(leader)`; during warm-up the runner sets the context variable `lite_eval.targets.base.warming_up` to true, and a Target can use it to leave the warm-up out of its own statistics.
- **`--crosscheck`**: for each retrieval scorer, take the ranks from each score's evidence and recompute with ranx 0.3.21 (computed for each repeat separately, then averaged); if it differs from lite-eval's value by more than 1e-9, a `crosscheck_mismatch` warning is given; without the `[ranking]` extra installed it is a config error.

### 18.22 Integrating the Viewer (decided in E3)

- **Modules**:
  - `index.py`: the run directory (`FileCatalog`) and the SQLite index (`IndexCatalog`) implement the same `RunCatalog` interface (`list_runs`, `trend`), and the same read tests run against both implementations.
  - `views.py`: the data the pages need (run detail, per-case, case detail, datasets, comparison). Loading and assembling are separate: `open_run` loads a run and its dataset cases once, and `rows` and `detail` assemble from the loaded data; HTTP loads once per request, and export and `triage` load once and then assemble case by case. Finding a dataset by name reads only `dataset.yaml` and `cases.jsonl` (the corpus is not hashed). Both the server and the HTML export take their data from here, so the exported page is the same as what the Viewer shows.
  - `annotations.py`: reading and writing per §18.9.
  - `server/`: `guard.py` (§18.10), `app.py` (routes), `run.py` (startup and `--check`), `paths.py` (where the build output is; it does not depend on the `[server]` extra, since export needs it too).
- **Index**:
  - It stores only each run's list row (`RunRow`) and the columns used for filtering; which run is the baseline is read from `baselines.yaml` fresh on every query and is not in the index (switching the baseline changes no run.json).
  - When a request comes in and more than 5 seconds have passed since the last scan, it does one incremental scan (of §18.15's "every 5 seconds or on a request" the latter is taken, with no separate background thread).
  - A run.json that cannot be read (hand-broken, or being replaced) is skipped and does not make the Viewer error.
- **Filters of the run list**: suite, config, status, pack, dataset name and version, whether it is a baseline. A run itself has no tags, so the "filter by tag" of §10.1 lands on these fields.
- **Case content**: found by the `case_hashes` in the run record, looking for a dataset of the same name under `<root>/datasets/*/` (decided by the owner 2026-11-14: case content is not stored again in the run directory):
  - same hash: `current`;
  - the case is still there but its content changed: `changed`, showing today's content and marking it;
  - the dataset or case is gone: `missing`.
  - "The same case's results in other runs" lists only runs with the same content hash, at most 20, newest first.
- **API** (paths all under `/api`; the spec is committed in `backend/openapi.json`, and the test `test_openapi_spec_is_current` fails when it is out of date):
  - read: `health`, `runs`, `runs/{suite}/{run_id}`, `…/cases`, `…/cases/{case_id}`, `compare?baseline=&candidate=` (`<suite>/<run_id>`), `datasets`, `datasets/{name}/cases`, `trends?suite=&metric=[&config=]`, `annotations`;
  - write: `POST annotations`, where the server fills in id, author and time;
  - errors are uniformly `{"error": {"message", "code"}}`: 404 `not_found`, 409 `conflict`, 400 `bad_request`, 403 `forbidden_host` / `forbidden_origin` / `missing_token`, 413 `too_large`; a request validation failure is FastAPI's default 422.
  - When exporting the spec, a single-element `enum` that duplicates `const` is removed: pydantic 2.9 (the declared lower bound) writes this extra item and 2.14 (the locked version) does not, and the two mean the same.
- **Security (implementation of §18.10)**:
  - the body of a write request is read in full in middleware before being handed to the app, and over 16 KB returns 413, whatever `Content-Length` says;
  - the token is generated randomly per process and injected into `<meta name="lite-eval-token">` when `/` is returned, and the page carries `Cache-Control: no-store`.
- **`lite-eval view`**:
  - `--root` defaults to the current directory (when it has `runs/` or `suites/`), otherwise `./evals`, and if neither exists it is an error (exit code 2).
  - `--check` without `--root` uses an empty temporary directory, so it does not take the lock of a running Viewer; when `/` or `/api/health` is not 200 the exit code is 3.
  - A hidden option `--static` names another frontend build directory (for tests and e2e).
  - `fastapi` and `uvicorn` are in the `[server]` extra; when they are not installed, `view` gives a one-line install hint.
- **`lite-eval triage <run>`**: lists, one by one, the misses that do not yet have a current triage (input, expected, output, each cut to 400 characters), triage with r / l / e / u, s to skip, q to quit; when someone else just triaged the same one first, it says so and continues with the next.
- **`lite-eval report --html`**:
  - The argument is a run directory, or `<baseline directory>..<candidate directory>` (split at the `..` where both sides are directories, so a relative path such as `../runs/x` still works).
  - The page is the Viewer's own build: the CSS is inlined as `<style>`; the only JS file becomes a module script with a `data:` URL, so a `</script>` appearing in the JS does not end the tag; the data is escaped per §18.12 and put into `#lite-eval-data`.
  - The data format is `views.ExportPayload` (a pydantic model): `{kind: run | compare, generated_at, lite_eval_version, start, runs: {"<suite>/<run_id>": {row, detail, rows, cases: {case_id: CaseDetail}}}, comparison}`. `start` is the route the page opens at (a single run opens at its run page, a comparison opens at the compare page). This model is written into the OpenAPI spec as a component (`openapi()` in `server/app.py`), and the frontend types are generated from it, not written by hand. "Other runs" in case detail are looked for only among the exported runs.
  - Masking is done by type, with the same code as the Viewer: output, scores, warnings, the target's config and info use the union of the exported runs' rules plus `--redact`; ids, hashes and dataset content are not changed (§18.12: datasets are not masked and are scanned at load). So in a comparison export, one run's text must also satisfy the other run's rules.
- **Frontend (decided in E3b)**:
  - Routing uses hash history: the Viewer and an export file opened from disk share one set of routes, and the server does not need to rewrite arbitrary paths back to `index.html`.
  - No code splitting (rolldown 1.2.19's `output.codeSplitting: false`); the build result is only one JS and one CSS file; only system fonts are used, with no font files bundled. This is what lets the export be a single file.
  - Data goes only through `src/data/source.ts`: `liveSource` uses the generated client, and `embeddedSource` reads `#lite-eval-data`. The exported page is read-only: it cannot write an annotation, and has no datasets page or trends page. An exported comparison opens directly on the compare page.
  - The backend's operationId takes the route function's name (`generate_unique_id_function`), and the generated client's function names are like `listRuns()`, `getCase()`.
  - Visual direction: dense-data (`PRODUCT.md`, `.impeccable/surfaces/frontend-src.md`). Only status colours are used: orange for worse, blue for better (the Okabe–Ito palette, verified in both themes with dataviz's validation script), and each status carries both an icon and text; a difference that is not significant is neutral grey.
  - Signature designs: the interval bar (all differences share one scale centred on zero; an interval that crosses zero is drawn neutral grey) and the case barcode (one cell per case, with cells for errored and not-generated cases hatched diagonally, and clicking a cell opens that case).
  - Keyboard: on lists J/K moves, Enter opens, X ticks for comparison; on the case page [ ] switches to the previous / next case, T triages; in the dialog R/L/E/U choose the category and Cmd/Ctrl+Enter saves.
  - e2e: `backend/tests/e2e/viewer_fixture.py` uses the fake target to produce real run records, generates the two kinds of export, and starts the Viewer on port 8722; Playwright covers the acceptance flow of §16 E3.
- **Packaging (decided in E3b)**: CI's `package` job builds the frontend per §18.14 → `uv build` → checks that the wheel has `server/static/index.html` → installs `wheel[server]` in a clean venv → `view --check`. When a `v*` tag is pushed, the `release` job (the only job with `contents: write` permission) publishes the wheel and sdist with `gh release create`.
- **API additions from E3b's /simplify** (all only add fields):
  - `CaseDetail` carries `dataset`, `miss`, `error` and `prev_case_id` / `next_case_id` (in the order of cases in the run), so the case page sends only one request and does not need the whole case list;
  - `CaseChange.direction` (worse / better) and `Comparison.baseline_config` / `candidate_config`. Which side counts as worse is given by the backend, and the browser only displays and does not judge (frontend rule 2);
  - the `limit` cap on the case list is 100000, and it no longer silently cuts at 1000;
  - all frontend query keys and parameters are in `src/data/queries.ts`. Run records are immutable, so run-related queries never go stale; only an annotation invalidates the three kinds of queries `case`, `cases`, `dataset-cases`.


### 18.23 Memory pack, HTTP and subprocess Targets, external benchmarks (decided in E4)

- **memory pack** (decided by the owner 2026-11-14: build only the scorers that have a user):
  - Output convention: `value` (or the part `field` points to) is the list of recalled memories, each a string or a dict (the text is under the key named by the `text` parameter, default `content`).
  - `hop<n>` (from 1) and `all_hops`, with expected `{hops: [[keyword…]…]}`. A hop is a hit = there exists a memory that, compared with `str.lower`, contains all the keywords of that hop; this is word for word the same as lite-mem's `_has`: no casefold, no whitespace merging, and keywords spread over two memories do not count. Each question is 0 or 1 (`verdict=value`), and the evidence records the memory that hit; the mean times the number of questions is lite-mem's count. When a case has fewer hops than n it is a scoring error, not a 0 score.
  - Default scorers `hop1, hop2, all_hops`. Evidence recall gets no scorer of its own: write the expectation `{relevant: [session or dia_id]}` and use the retrieval pack's `recall@k`; content that must not appear uses the existing `must_not`.
  - Deferred: extraction (`{facts}`) and update (`{superseded, kept}`) and so on, until a project needs to test them; refusal needs a judgement of whether the answer is right, and goes to E5's model judge.
- **The Target's optional members**: the runner reads them with getattr and uses defaults when they are absent; all are listed in the `Target` docs in `targets/base.py`: `deterministic`, `share_setup_from` (§18.18), `serial`, `abandoned()`, `artifacts()`, `describe()`. The HTTP and subprocess Targets share `SpecBase` (`billing`, `versions`, `deterministic`) and `SpecIdentity` (`versions`, `info`, `billing`, `setup_identity` are derived from the declarations in the block; `setup_identity` contains the whole config, so each config does its own setup and there is nothing to share).
- **How Targets are written in a Suite**: each item of `targets` has exactly one of `python`, `http`, `command`, plus `configs`. Keys in a config other than `name`: for a Python Target they are keyword arguments; for an HTTP Target they are the variable `config` of the request body template; for a subprocess Target they are the `config` field in each request line. All three record these keys into `target.config`, so they enter the cache key. `billing` (default `unknown`; in CI mode it must be written `free` to run, §18.13), `versions` (versions that change the output, entering the cache key) and `deterministic` are written in the `http:` / `command:` block. `prepare_targets` prints every command to be run and every URL to be accessed to stderr.
- **HTTP Target** (implementation of §18.11):
  - Uses httpx2's `AsyncClient`, does not follow redirects; when `timeout_s` is not written, only the Suite's `timeout_s` applies.
  - A `${NAME}` in a header is taken from the environment when the Target is built, and a missing one is a config error (exit code 2, nothing is called); the record has only the template.
  - When the server echoes the request back (a 401 page, a debug endpoint), the resolved values appear in the reply: before the reply, trace and error message leave the Target, it replaces every environment variable value it used with `«redacted:env:NAME»`, and does not chain the original exception (found by the security review of commit `0f1a056`). An error page is replaced as a whole first and then cut to 500 bytes (cutting first could leave the first half of a secret), and it also replaces the JSON-escaped and URL-encoded forms of the value (`quote`, `quote_plus`) (found by the security review of commit `69ee17f`).
  - From E4d this is handed to the runner's known-value masking (§18.12): the HTTP Target only declares the resolved values with `secrets()`; as soon as a reply arrives it is masked as a whole once, then the error page is truncated, parsed and validated (the runner sees only the truncated text, and pydantic's validation error shows only 24 characters on each side of a value; E4d's /simplify found that the plan's "mask only before truncating the error page" was not enough, and moving it to the runner's step once left both ends of the secret in a validation error). A successful reply must be UTF-8 (RFC 8259), and an error page is shown as it is. It also recognises the `\/` escape, HTML escapes and lowercase percent-encoding.
  - With a request body and no `Content-Type` written, the default is `application/json`; the template uses `SandboxedEnvironment` and `StrictUndefined`, with the variables `case` (the case's JSON) and `config`; a template syntax error is a config error at build time, and an access the sandbox refuses is recorded as that case's `exception`.
  - JSONPath supports only `$`, `.key`, `[n]` (which may be negative), `[*]`, and is stricter than RFC 9535: under `[*]`, a missing key in one item also counts as not found (`invalid_output`), so an item is not quietly dropped. `[*]` on an empty list gives an empty list: no retrieval hits is a legal output, not "not found". The object `trace` points to is validated as a `Trace`, and one that does not conform is likewise `invalid_output`.
  - A timeout (httpx2's `TimeoutException`, a subclass of `TransportError`, so it is tested first) is recorded as `timeout` and not retried; other transport errors are retried per §6.
  - Latency uses the runner's wall clock (the same for success and failure), and uses the reply's own `latency_ms` when its `trace` carries one.
- **Subprocess Target** (implementation of §18.11):
  - `cwd` is relative to the directory of the Suite file, and when not written it is the user's repository root (the same as the Python Target's import root); the record holds the value written in the Suite, not the resolved local path. `env_allow` defaults to `[PATH]`.
  - Which of the variables `env_allow` lets through are secrets (E4d, masked by value, §18.12): names that contain `KEY`, `TOKEN`, `SECRET`, `PASS`, `CREDENTIAL`, `AUTH` (case-insensitive, `SECRET_NAME` in `targets/command.py`); when `secret_env: [...]` is written, it takes precedence. Not every allowed variable is treated as a secret: otherwise ordinary values such as `OLLAMA_HOST=127.0.0.1:11434` or a model name would be masked in every output and quietly change scoring (found by E4d's /simplify, and the plan's original "treat all as secrets, with a few exceptions" was changed because of it).
  - When stderr is truncated at the 5 MB limit, the end of the kept part drops the length of one longest form (which may be the start of a secret), counted as "not kept" (the security review of commit `9d8e815`); after truncation nothing more is kept, even when trimming the end frees room again (the security review of commit `412af4b`: otherwise a later chunk would attach after the gap and could be truncated once more).
  - Each line the program outputs is first masked by these values when read in, and only then parsed, truncated or quoted: the prepare-line error quotes the first 200 characters, an invalid reply is cut to 500, pydantic's error message shows only 24 characters on each side of a value, and a truncated secret cannot be recognised by anyone. A reply that is not UTF-8 is recorded as `invalid_output` and is not replaced with other characters.
  - The Target declares `serial = True`, and the runner sends it only one case at a time: otherwise, at `concurrency=4`, cases queued for the lock would spend their own timeout waiting. The Target has its own lock too.
  - A reply can carry `error: {kind, message}`, which the program uses to report its own failure (for example a truncated model reply).
  - Restart when out of sync: a case counts as "in progress" from writing the request to reading back and checking the reply; if it ends any other way (timeout, a reply that is not JSON, a `case_id`/`repeat` mismatch, an overlong line), stdout may be misaligned with the requests, and the process is restarted before the next case. These restarts do not count toward "restart only once on a midway exit". A restart kills the old process directly without waiting; not being ready after a restart counts as one exit.
  - Process group: started with `start_new_session`, restart and teardown signal the whole process group, and after the end SIGKILL is sent once more to the group to clean up the remaining child processes; otherwise, when a wrapper command (`uv run python x.py`) is killed, the process it started is still alive. Note that asyncio's `Process.wait()` returns only when the process has exited **and** all the pipes are closed: when a child process inherited the pipes, the wait lasts until it ends.
  - A startup failure (timeout, the first line is not ready) kills the process before reporting the error: a Target whose setup failed is not in the runner's teardown list, and without cleanup it would leave the process behind.
  - Exit between cases: if the process is found already exited before a request is sent, it counts as one exit just the same (toward restart only once), but this case has not yet been sent, so it is sent to the restarted process and is not recorded as a failure. An exit timed exactly around the send is still handled as a midway exit.
  - Midway exit: the current case is recorded as `exception`, and the process is restarted before the next case; after the second exit the Target gives up, and the remaining cases are recorded as `exception` ("not run"), the optional method `abandoned()` returns the reason, and the runner marks the run `partial` and adds the warning `target_abandoned`.
  - stderr keeps at most 5 MB, with the number of excess bytes written at the end; note lines that start with `[lite-eval]` are not subject to the limit (few and short, but they explain what happened to the process); restart and termination each log a `[lite-eval] …` line there. The optional method `artifacts()` returns `{file name: text}`, which the runner processes with this run's masking rules and writes into the run directory (before `run.json`; it cannot have the same name as a record file).
- **External benchmarks** (change: §16 says "loaders", and the implementation is an import command, not a new form inside the Suite): `lite-eval dataset import longmemeval|locomo <official file> <new directory> [--limit N] [--include-abstention] [--name]` writes the official file as an ordinary dataset directory (`--include-abstention` is only for LongMemEval). This way the hash, the secret scan, `rescore` and the Viewer's lookup of case content by hash all need no change; if it were loaded inside the Suite, the Viewer could not find these cases.
  - `dataset.yaml` records `license` and `source: {name, url, file, sha256}` (the official file's sha256, computed from the file as a stream, without keeping another copy of the original in memory), `version` is the first 12 characters of the file's sha256 (with `+firstN` added when `--limit` is used), and `description` states what was cut and skipped. `DatasetRef.license` (a field added in E4) carries the licence into every run record.
  - The import is first written fully into a temporary directory next to the target and then renamed, so a failure leaves no half-written directory; an existing target directory is refused.
  - The official file is read item by item and written item by item (`iter_json_list`: the standard library's `json.JSONDecoder.raw_decode`, no added dependency), and only the question being parsed is held in memory (changed in E4b: reading the whole file, S's 277 MB measured 2.4 GB, and M's 2.7 GB would need about 24 GB, with 34 GB on this machine). `dataset.yaml` is written last, because how many were skipped is known only after reading everything.
  - **LongMemEval** (MIT): one case per question, with `question_id` as the id; input `{question, question_date, haystack: <question_id>}`, and this question's history sessions are in `corpus/<question_id>.json`: `[{id, date, turns: [{role, content}]}]`, in file order (`has_answer` is a label and is not given to the system under test). Changed in E4b: in E4a the history was put directly in the input, and M's 500 questions, 2.7 GB in total, would all enter the cases, so the runner would have to read all of it into memory. Note: the secret scan (§18.12) has scanned only `cases.jsonl` since E1 and never `corpus/` (LoCoMo's conversations and lite-rag's documents are the same), so once the history moved into the corpus it is not scanned either; whether to scan the corpus is an open question (raised by E4b's /simplify, see the E4b record in §16). In S, 80% of sessions appear in only one question, so de-duplicating by session saves little, and so they are stored per question. Expected `{relevant: answer_session_ids, answer}` (a numeric answer is converted to text); label `question_type`. Abstention questions whose id ends in `_abs` are skipped by default (consistent with the official retrieval eval), and when kept they get the label `abstention: "true"`. `--limit` takes the first N retained questions in file order.
  - **LoCoMo** (**CC BY-NC 4.0**: not for commercial use, attribution required): each conversation is written only once, in `corpus/<sample_id>.json` (speakers, sessions sorted by number, turns kept as is, including `dia_id`); one case per question-answer, with id `<sample_id>:q<nnn>`, input `{question, conversation: <sample_id>}`, and the Target reads the conversation from `dataset.corpus_dir` in `setup`. Copying the conversation into its roughly 200 questions would mean hashing, scanning and sending it 200 times in every run (found by E4a's /simplify). Expected `{relevant: evidence, answer}`. Category 5 has no correct answer: `answer` is empty, and the trap answer is kept separately in `adversarial_answer` and not treated as the answer. The label `category` uses only the original number, as the official release does not state what each number means.
  - Tests use only the hand-written fictional samples in `tests/data/loaders/` (in the format of the official files), and do not commit official data.

### 18.24 Model judges, calibration, KB export (decided in E5a)

- **Config**: the Suite's `judges: {<role>: JudgeSpec}`, a scorer is written `{judge: {rubric, role?, calibration?}}`, and `role` defaults to `rubric`; a missing role is a config error. `JudgeSpec`: `provider` (`ollama` | `openai`), `model`, `base_url`, `channel` (defaults to provider), `temperature` (default 0), `think` (`false` / `true` / a level string), `api_key_env`, `billing` (default `unknown`), `price` (per million tokens), `max_tokens`.
- **Identity**: `{provider, channel, model, temperature, think}`, plus `rubric` and `prompt_version` (the first 12 characters of the sha256 of the two templates' content, not numbered by hand), is the scorer's identity, and the scorer version is its hash; `base_url` and the key are not in it (the same model on another machine is not a change of instrument). If any item changes, the comparability check of §18.3 reports not comparable.
- **Call** (`judges/client.py`, the only code in the whole package that calls a model; it uses only httpx2 and does not depend on the openai SDK, which is why the `[judge]` extra was removed):
  - `ollama`: `POST /api/chat`, `stream: false`, `format: <JSON schema>`, `think`, `options.temperature`; usage is taken from `prompt_eval_count` / `eval_count`, and the `thinking` in the reply is recorded as is.
  - `openai` (OpenAI-compatible, which llm-gateway uses): `POST /chat/completions`, `response_format: {type: json_schema, json_schema: {name: verdict, strict: true, schema}}`, and `reasoning_effort` is sent when `think` is a string; usage includes `completion_tokens_details.reasoning_tokens`.
  - The parameters on both sides were checked with context7 on 2026-11-18 (the Ollama 0.40.15 docs, the OpenAI API reference), and one real round trip with qwen3:8b was done under `-m ollama`.
  - The key is taken from `api_key_env`, and a missing variable is a config error; the key is masked as a known value (§18.12) in error messages, records and the whole run's masking. A timeout or non-2xx is recorded as `judge_call_failed`; transport errors are retried by the rules of §6, and after they are exhausted it is likewise `judge_call_failed`. No fallback.
- **Reply**: validate `{verdict, reason, evidence}` against the rubric's schema (the `score` written in §9 is not in the schema: the verdict is binary, and the scorer's score comes from the verdict), and `verdict` may take only the values the rubric lists; an invalid one is recorded as the scoring error `invalid_judge_output`, and the raw reply is kept in `JudgeRecord.raw`. `Score.value` = 1 (the verdict equals the rubric's `passing`) or 0, with `verdict=value`; evidence is `{verdict, evidence}`. `JudgeRecord` gained `think` and `usage` (fields only added).
- **Async scoring**: the scoring phase became `ascore_outputs`; deterministic scorers are still computed synchronously (no task is created), and judge calls run concurrently, with concurrency = the Suite's `concurrency` (the budget check, the call and the accounting are inside one slot, and backoff is outside the slot, so that a call waiting to retry does not hold a slot), and the timeout of each call is the Suite's `judge_timeout_s` (default 120). The budget ledger counts the judge's cost too, and once it is exceeded the remaining judges are not called, they are recorded as scoring errors, and the run is marked `partial`. Outputs where the system under test itself errored are not sent to the judge (they already count as failures per §18.8).
- **Cost**: `CostSummary` gained `judge_calls`, `judge_input_tokens`, `judge_output_tokens`, `judge_reasoning_tokens`, `judge_cost_usd`, kept separate from the cost of the system under test; the terminal report has a `judges: …` line. The CI billing guard (§18.13) governs judges too: a judge that is not `free` is refused in CI.
- **Judge results are not cached**: not done in this stage; the cache key would have to include the output content, and it can be added when the repeated cost is real. `rescore` judges the stored outputs again (it calls the judge and never calls the system under test), using the Suite's current `judges` and budget, and the CI billing guard applies as well (when no options are passed it goes by the `CI` environment variable). The `base_url` that the calibration Target records into `info` has user info, query and fragment removed.
- **Combination**: a judge cannot go inside `all_of` / `any_of` / `weighted` (a config error): combinations are synchronous, and a judge's score must be visible on its own.
- **Calibration**:
  - A calibration run = one run of the `judging` pack, whose Target is `lite_eval.judges.target:judge_target` (`JudgeTarget`), with the config being the judge's identity; the case `input` is what the judge sees, `expected: {label}`, and constructed cases carry the label `construction`. When the judge's reply is invalid, that case is recorded as `invalid_output`.
  - A scorer writes `calibration: {run: <run directory relative to the eval root>, min_agreement}` (the form is validated when the scorer is built). Before the run, before anything is called, `judges/calibration.py` checks (reading only `run.json`): the identities of the scorer and of the calibration Target both come from the same `Rubric.identity`, and calls both go through `Rubric.ask`, so during calibration the judge sees the same prompt as in use; the run exists; it is a judging run (the Target is `judge` and it has `label_agreement`); its config is identical item by item to the current judge's identity (the error lists the first difference, for example `think False -> True`); the status is `complete`; the agreement rate ≥ `min_agreement`. Failing any one is a config error (exit code 2).
  - When `calibration` is not written: the run carries the warning `judge_uncalibrated` and `ScorerRef.calibrated = false`; a gate rule that reads this judge's metric (or its `:pass_rate`) is a config error, while other rules in the Suite are judged as usual, so an exploratory judge can sit together with existing gates (decided by the owner 2026-11-18; before, one uncalibrated judge made the whole gate refuse).
  - The terminal report lists a confusion matrix for a judging run (expected → verdict: count), computed from the scores' evidence (`analysis/judging.confusion`), with no contract field added.
- **Construction tool** (`judges/construct.py`, methodology §2): `change_number` (pick a number at random and change it; citation numbers do not count as numbers), `swap_answer`, `insert_sentence`, `append_section` → `unsupported`; `drop_citations` → `grounded` (the grounded rubric does not grade citation marks). All choices use a `random.Random` supplied by the caller, and the same seed gives the same calibration set. The plan's "add a forbidden phrase" was not done: that is governed by the `must_not` rule and is not a label of the grounded judge.
- **extraction pack**: the output is a list of items (or `{items: [...]}`), each a string or `{text}`; expected `{items: [...]}`. `item_recall`: an expected item appears in some output item (whitespace merged and case-insensitive by default, as `contains` does), and the evidence lists those missed; `item_duplicates`: the share of duplicate items among the output items (lower is better), and the evidence lists the duplicates.
- **grounded pack**: default scorers `{contains: {field: answer, expected: must}}`, `{must_not: {field: answer}}`, `{judge: {rubric: grounded}}` (which requires the Suite to have `judges.grounded`). `Pack.scorers` therefore allows the dict form.
- **KB export** `lite-eval export kb <run directory…>` (`api.export_kb`): for the runs of one Suite, one line for each run and each exported metric, written to stdout. The fields follow the `results.jsonl` schema of the KB's `global/llm-evals/README.md` (the 2026-11-18 version, that is the fields and values its `scripts/check.mjs` checks), and each line is checked against these rules before being written, so a line the KB would reject is reported here as a config error. What can be computed from the record: `date` (the start date, in the time zone of `kb.timezone`, an IANA name, default UTC; a New Zealand morning run is the previous day in UTC, so the owner's Suite writes `Pacific/Auckland`; it is written in the Suite and not taken from the local time zone, so the same run exports the same on whichever machine), `n`, `trials`, `value` (kept to 4 places), `values` (when repeated), `cost_usd` (per question, system under test + judge), `variant` (the config name); what the record does not have is written in the Suite's `kb:` section (`KbSpec`, where task / effort / trust take the KB's values and grader follows the KB's regex, and a mistake is reported when the Suite is loaded; `eval`, `project`, `task`, `dataset`, `grader`, `metrics: {lite-eval metric: KB metric name}`, `trust`, `notes`, and optionally `vendor`/`model`/`effort`/`timezone`). When not written, vendor and model are taken from the Target's config or info; the judge Target's effort comes from `think` (off = `n/a`, on = `default`, a level as is). Tests: after exporting the committed v1 sample run, it is word for word the same as `tests/data/kb/v1-run.jsonl`; `-m kb` (only when this machine has the KB and node) appends these lines to a copy of the KB and runs its `check.mjs`, which must give 0 errors; CI has no KB and relies on the checks above as the safety net.
