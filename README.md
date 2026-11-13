# lite-eval

A general evaluation module for AI applications — RAG, memory, extraction, judging, grounded writing, agents.
It answers one question for any system you can call: **did this change make it better or worse, and can you trust
that?**

- Any system under test: a Python object, an HTTP service or a subprocess.
- Generate once, score many: outputs are cached; deterministic scorers first, calibrated model judges second.
- Immutable run records with full provenance; paired comparisons with significance; honest CI gates.
- A local Viewer for scores, comparisons, every case's trace and miss triage.

Status: design only (2026-11-13). Read [`docs/lite-eval-design.md`](docs/lite-eval-design.md).
Sister projects: [lite-rag](https://github.com/ArthurGao/lite-rag), [lite-mem](https://github.com/ArthurGao/lite-mem).
