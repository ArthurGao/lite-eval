@AGENTS.md

# Claude Code notes

- At the start of a session: read design §16 (`docs/lite-eval-design.md`) for the current stage and its acceptance,
  skim §3 (lessons), and the project KB at `/Users/arthurgao/Docs/Knowledgebase/projects/lite-eval/` once it exists.
- The design doc is long: read it by section (`rg -n '^#' docs/lite-eval-design.md`), not whole.
- Use plan mode for anything that changes a contract (§5), the runner (§6) or the comparison statistics (§8.2), or
  touches both backend and frontend.
- Relevant user-level skills: `llm-model-eval` (judges, comparing models), `rag-design` (retrieval metrics),
  `testing-and-ci`, `llm-app-architecture` (roles, structured output, no fallback chain), `dev-machine` (ports,
  Ollama), `design-direction` (the Viewer's look), `kb-maintain` (writing the KB), `dataviz` (charts in the Viewer).
- Never answer a ranx / FastAPI / pydantic / Typer / Inspect AI / TanStack question from memory: check context7 or
  the installed version's source, and note the verified version in a comment where it matters.
- **Stage close-out, for every stage, in this order** (`AGENTS.md` Workflow step 5):
  1. `/simplify` on the stage's full diff; apply its fixes.
  2. Codex, from the repo root, read-only, on the stage's whole diff:
     `codex exec -s read-only -o <out.md> "Review \`git diff <commit before the stage>\` (includes uncommitted
     changes) … focus … report severity, file:line, scenario, fix" < /dev/null`.
     The `< /dev/null` matters: with a piped stdin, `codex exec` waits for stdin to close and hangs in a
     background shell. `codex review --base` refuses a focus prompt, so prefer `exec`.
  3. Fix every finding that holds up (a bug gets a failing regression test first); write down the rejected ones
     with the reason.
  4. Re-run every check (read summary lines, e.g. `rg 'passed|failed'`, never a bare `| tail -1`), then report and
     commit. Do not start the next stage before this is done.
