---
version: 1
slug: "frontend-src"
primary_target: "frontend/src"
related_targets: []
---

# lite-eval Viewer

Scope: the whole Viewer SPA (`frontend/src`), served by `lite-eval view` and exported as one HTML file. Mode: Operate.
Audience and job: see PRODUCT.md (owner + adopting engineers, equally); main job is compare-then-triage after a change.
Constraints: system fonts, no network assets, one JS + one CSS file; light and dark; WCAG AA; colour-blind safe;
full keyboard (J/K lists, single-key triage).

## Direction contract

THESIS: A result is a number with its doubt attached. Refuses the dashboard default of big green/red KPI tiles: every
delta is shown with its interval and the count it rests on, and a non-significant one reads as neutral, not as a win.

OWN-WORLD: dense-data (pinned). Cool slate-tinted neutrals, one ink-blue accent for selection and links only;
Okabe–Ito status pair (orange = worse, blue = better, grey = not significant), each always with an icon and a word.
System sans 13px, tabular figures right-aligned; ui-monospace for ids, hashes, times. Square-ish 4px corners,
1px hairlines, no shadows except overlays.

STORY: Open → see the newest runs and which have warnings → pick baseline + candidate → read deltas with their
intervals → open the regressed cases first → classify each miss without leaving the keyboard.

FIRST VIEWPORT: Top bar (product name, Runs / Compare / Datasets / Trends, theme toggle). Runs table fills the
page: suite, config, dataset@version, status, the suite's main metrics, cost, latency, started; baseline, partial,
warning and synthetic marks inline; select two rows → "Compare" action in the table toolbar.

FORM: Dense operator console on standard web controls. Signature move: the interval strip — each delta drawn as a
short horizontal range on a shared zero tick, hatched grey when it crosses zero; and the case strip — one cell per
case, coloured by outcome, clickable into the case. Status is folded into the run marks (shown
only when a run is not complete), not a column. seed: none (direction pinned by the brief; no concept roll
ran, so there is no seed reference; the owner accepted this under new-work.md's pinned-direction rule,
2026-11-14)

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
