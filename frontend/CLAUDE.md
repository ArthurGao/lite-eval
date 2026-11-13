@AGENTS.md

# Claude Code notes (frontend)

- Load the `design-direction` skill before choosing the Viewer's overall look, and `dataviz` before any chart.
- Build pages in this order: Runs → Run detail → Case detail → Compare → Triage → Datasets → Trends.
- After a backend API change, run `pnpm generate-client` before touching pages that use it.
- Run `pnpm typecheck && pnpm lint && pnpm test` before finishing; run `pnpm e2e` for flow changes, and show a
  screenshot for visual changes.
