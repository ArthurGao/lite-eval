@AGENTS.md

# Claude Code notes (backend)

- Before changing a contract, re-read design §5 and write the compatibility test (old record still loads) first.
- For the runner's cache, the comparison statistics and the gate, write or extend the tests before the
  implementation and run only those while iterating (`uv run pytest tests/unit -k <name>`).
- After changing any server route or schema, regenerate the frontend client (`pnpm -C ../frontend generate-client`)
  and say so.
- When a test needs a model, use `FakeJudge`; when it needs a system under test, use `FakeTarget`. If a behaviour
  cannot be tested without a real model, say so explicitly instead of calling one.
