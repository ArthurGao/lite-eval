---
name: lite-eval Viewer
description: A dense, local operator console where every result is a number with its doubt attached.
colors:
  canvas: "#eef1f4"
  surface: "#fbfcfd"
  raised: "#ffffff"
  ink: "#111827"
  ink-2: "#4b5563"
  muted: "#6b7280"
  rule: "#e3e7ec"
  rule-strong: "#c9d0d8"
  select: "#e3e8f0"
  hover: "#f1f4f7"
  focus: "#3d4f7a"
  worse-mark: "#c4560a"
  worse-text: "#a8470a"
  worse-wash: "#fbeee5"
  better-mark: "#2a6fd0"
  better-text: "#1f5cb0"
  better-wash: "#e8f0fb"
  neutral-mark: "#7d8794"
  warn-mark: "#b7791f"
  warn-text: "#8a5a12"
  warn-wash: "#fbf3e2"
  canvas-dark: "#0f1215"
  surface-dark: "#15181c"
  raised-dark: "#1b1f24"
  ink-dark: "#e6e9ee"
  ink-2-dark: "#b6bdc8"
  muted-dark: "#8b94a3"
  rule-dark: "#262b32"
  rule-strong-dark: "#353c45"
  select-dark: "#252c37"
  hover-dark: "#1d2228"
  focus-dark: "#8ea6d6"
  worse-mark-dark: "#d46a28"
  worse-text-dark: "#ee9a63"
  worse-wash-dark: "#2b1d14"
  better-mark-dark: "#4d8fe6"
  better-text-dark: "#7db0f0"
  better-wash-dark: "#14223a"
  neutral-mark-dark: "#77808e"
  warn-mark-dark: "#e0b04a"
  warn-text-dark: "#e8c06a"
  warn-wash-dark: "#2a2312"
typography:
  headline:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.45
    fontFeature: "tnum"
  headline-id:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
    fontSize: "15px"
    fontWeight: 600
    lineHeight: 1.45
  title:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.45
    fontFeature: "tnum"
  body:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "tnum"
  label:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.45
    fontFeature: "tnum"
  micro:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "11px"
    fontWeight: 500
    lineHeight: 1.45
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.45
rounded:
  cell: "1px"
  sm: "0.25rem"
spacing:
  hairline: "2px"
  xs: "0.25rem"
  sm: "0.375rem"
  md: "0.5rem"
  lg: "0.75rem"
  xl: "1rem"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 0.625rem"
    height: "1.75rem"
  button-quiet:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 0.625rem"
    height: "1.75rem"
  button-quiet-hover:
    backgroundColor: "{colors.hover}"
  select:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0 0.375rem"
    height: "1.75rem"
  tag-plain:
    backgroundColor: "{colors.select}"
    textColor: "{colors.ink-2}"
    typography: "{typography.micro}"
    rounded: "{rounded.sm}"
    padding: "0 0.375rem"
    height: "18px"
  tag-warn:
    backgroundColor: "{colors.warn-wash}"
    textColor: "{colors.warn-text}"
    typography: "{typography.micro}"
    rounded: "{rounded.sm}"
    height: "18px"
  tag-worse:
    backgroundColor: "{colors.worse-wash}"
    textColor: "{colors.worse-text}"
    typography: "{typography.micro}"
    rounded: "{rounded.sm}"
    height: "18px"
  kbd:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink-2}"
    typography: "{typography.micro}"
    rounded: "{rounded.sm}"
    padding: "0 0.25rem"
  section:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.sm}"
  table-row-selected:
    backgroundColor: "{colors.select}"
    textColor: "{colors.ink}"
  nav-link:
    textColor: "{colors.ink-2}"
    padding: "0 0.5rem"
  nav-link-active:
    textColor: "{colors.ink}"
  case-cell:
    rounded: "{rounded.cell}"
    width: "0.5rem"
    height: "1rem"
---

# Design System: lite-eval Viewer

## Overview

**Creative North Star: "The Instrument Readout"**

The Viewer reads like a calibrated instrument, not a dashboard. Every number sits next to the evidence that qualifies it: an interval, a p-value, a case count, the worth of one case. Doubt is drawn as well as written; a difference that is not significant is hollow or hatched grey and never borrows the colour of a win or a loss. The page is quiet so that the few marks that carry colour can be read at a glance.

It is a dense operator console built from standard web controls: system type at a 13px base, tabular figures, 1px hairlines, gently squared corners, and tables that fill the width. Cool slate neutrals do the structural work in both themes. Colour is spent only on status, and every status also carries an icon and a word, so the screen survives colour blindness, greyscale printing and a dark room.

The same build serves the live Viewer and a single exported HTML file opened from disk, so everything the eye sees ships inside one CSS and one JS file: no web fonts, no images, no network assets.

**Key Characteristics:**
- Dense tables at 13px with tabular, right-aligned figures; ids, hashes and times in monospace.
- Colour is status only: the Okabe-Ito orange (worse) and blue (better) pair, grey for not significant.
- Two signature marks: the interval strip and the case strip.
- Flat surfaces separated by hairlines; shadow only on overlays.
- Light and dark themes from one token set, toggled system / light / dark.
- Keyboard first: J/K lists, single-key triage, visible key hints.

## Colors

A cool slate neutral field with one functional pair of status hues; nothing decorative is coloured.

### Primary
- **Worse Orange** (`worse-mark`; dark `worse-mark-dark`): the mark for a regression, a miss, an errored case. Fills interval bands, the verdict icon, case-strip cells. Its text variant (`worse-text`) and wash (`worse-wash`) carry failure messages and the partial / failed run tag.
- **Better Blue** (`better-mark`; dark `better-mark-dark`): the mark for an improvement or a fixed case. Never used for selection, focus or links. `better-text` and `better-wash` exist for the same paired roles as orange.

### Secondary
- **Weathered Amber** (`warn-mark`, `warn-text`, `warn-wash`): self-check warnings and drift notes (a case whose content changed after the run). A caution, not a verdict.
- **Not-Significant Grey** (`neutral-mark`; 3.55:1 light, 4.46:1 dark against the surface): hollow points, hatched bands, the not-significant / no-change / incomparable icons, never-generated cells.

### Neutral
- **Slate Canvas** (`canvas`): the page behind sections and the sticky top bar.
- **Surface Paper** (`surface`): section bodies, tables, sticky first-column cells, the fade at a scrolling table's right edge.
- **Raised White** (`raised`): controls (quiet buttons, selects, kbd), the dialog and the chart tooltip.
- **Ink** (`ink`): primary text, the primary button fill, the active nav underline, the trend line.
- **Ink 2** (`ink-2`): secondary text, table headers, verdict words, the zero tick of the interval strip.
- **Muted** (`muted`): axis labels and the least important metadata.
- **Hairline** (`rule`) and **Strong Hairline** (`rule-strong`): row dividers and section borders; control borders, the interval track, pass cells.
- **Slate Selection** (`select`): the selected row, the chosen triage option, plain tags.
- **Hover Wash** (`hover`): row and quiet-button hover, skeleton bars.
- **Focus Slate** (`focus`): the 2px focus ring and the accent of native checkboxes and radios.

### Named Rules
**The Status-Only Rule.** Colour means worse, better, not significant or warning, and nothing else. Navigation, selection, focus, headings and links stay in the slate neutrals.

**The Mark-Not-Number Rule.** The data colour sits on the mark (icon, band, point, cell), never on the metric value, the delta or the verdict word, which stay in ink. The `*-text` tones are reserved for failure and warning messages and counts of failures.

**The Icon-And-Word Rule.** Every status is told by an icon and a word (or an accessible name when compact), never by colour alone. Errored and never-generated cases also carry a 135-degree hatch texture.

**The Slate-Selection Rule.** Selection and focus are slate, never the "better" blue, so a selected row can never be misread as an improvement.

## Typography

**Body Font:** the system sans stack (system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif)
**Label/Mono Font:** the system monospace stack (ui-monospace, SFMono-Regular, Menlo, Consolas, Liberation Mono, monospace)

**Character:** Native, unbranded and fast to read; the platform's own face at a compact size, with tabular figures everywhere so columns of numbers align. There is no display tier: the largest text on any page is 16px.

### Hierarchy
- **Headline** (600, 16px, 1.45): the page heading (run, comparison, dataset, triage).
- **Headline Id** (mono 600, 15px): a page whose subject is an id (case detail, the triage focus case).
- **Title** (600, 13px): section headers inside their hairline bar.
- **Body** (400, 13px, 1.45): table cells, prose, controls. Prose is capped near 60-80ch.
- **Label** (500, 12px): table column headers, captions, legends, key-hint rows, form labels.
- **Micro** (500, 11px): tags, kbd hints, chart axes, the p-value beside a strip.
- **Mono** (400, 12px): case ids, run ids, hashes, git shas, warning codes, inline commands.

### Named Rules
**The Tabular Rule.** The body sets `font-variant-numeric: tabular-nums`; numeric columns are right-aligned so digits line up across rows.

**The No-Web-Font Rule.** Only system stacks: the exported Viewer must render from one self-contained file with no network.

## Layout

The root font size is 13px, so every rem-based step is a multiple of 13px: the 0.25rem spacing unit is 3.25px, a 1.75rem control is 22.75px high. Media queries are not affected (breakpoints stay at 640 / 1024 / 1280px).

The shell is a 40px-tall sticky top bar on the canvas (product name, Runs / Compare / Datasets / Trends, theme toggle) over a centred main column capped at 1600px, with 0.75rem side padding on small screens and 1rem from `sm`. Pages stack full-width sections with a 1rem gap; detail pages split into two columns only from `xl` (2:1 on run detail, 1:1 on case detail), and triage uses an 18rem sticky queue beside the case from `lg`.

Tables fill their section. Cells use 0.75rem padding at the outer edges and 0.5rem between columns, with 0.375rem vertical padding, so a row is about 32px. Below `sm` the nav tightens its gaps and the theme toggle shows only its icon.

**The Sticky-First-Column Rule.** A wide table keeps its identifying first column sticky (Runs, Compare, cases) and its scroll container fades the right edge to the surface whenever more columns sit off-screen.

## Elevation & Depth

Flat by default. Depth comes from tone (canvas, then surface, then raised controls) and 1px hairlines, not from shadow. Shadows appear only on things that float above the page.

### Shadow Vocabulary
- **Dialog** (`box-shadow: 0 8px 28px rgba(0,0,0,0.18)`): the triage dialog over a 30% black scrim.
- **Tooltip** (`box-shadow: 0 4px 14px rgba(0,0,0,0.12)`): the trend chart's hover readout.

### Named Rules
**The Overlay-Only Shadow Rule.** A shadow means "this floats". Sections, tables, cards and buttons never cast one.

## Shapes

Gently squared corners (0.25rem, about 3.25px) on sections, buttons, selects, tags, kbd hints, the dialog and inputs; case-strip cells are nearly square (1px). Borders are always 1px: `rule` between rows and around sections, `rule-strong` around controls. The interval band has a 2px corner radius inside its SVG. Nothing is pill-shaped or fully round except the interval strip's 4px point.

## Components

### Buttons
Plain and compact; a button looks like a control, not a call to action.
- **Shape:** gently squared (0.25rem), 1.75rem high, 0.625rem horizontal padding, 13px medium label, optional 14px leading icon.
- **Primary:** ink fill with surface text and an ink border; one per toolbar or dialog (Compare, Save). Hover drops opacity to 90%.
- **Quiet (default):** raised fill, ink text, strong-hairline border; hover takes the hover wash. Transitions are 150ms colour only.
- **Disabled:** 50% opacity and a not-allowed cursor.

### Chips
- **Tag:** 18px tall, 11px medium, 0.25rem corners. Plain tags (baseline, synthetic, an active filter) sit on the selection slate in ink-2; warning and failure tags take their wash and text tone and lead with a 12px triangle icon.
- **Run marks:** a run's baseline, status, warning and synthetic tags appear together wherever the run is listed. Status is shown only when a run is not complete; there is no status column.

### Cards / Containers
- **Section:** surface background, 1px `rule` border, 0.25rem corners, no shadow. A hairline header bar at least 36px tall holds a 13px semibold title on the left and actions on the right.
- **Notes:** drift and warning notes inside a section use the amber wash with a 40% amber border.

### Inputs / Fields
- **Select and textarea:** raised (select) or surface (textarea) fill, strong-hairline border, 0.25rem corners, 1.75rem high for selects.
- **Checkbox and radio:** native controls tinted with the focus slate.
- **Focus:** every focusable element gets a 2px focus-slate outline with a 1px offset.
- **Error:** the message sits below in `worse-text`; a save failure is a worse-wash block.

### Navigation
The top bar sits on the canvas with a hairline below. Links are ink-2 at 13px; hover turns them ink; the active link is ink with a 2px ink underline flush with the bar's bottom edge. The exported page shows an "Exported page · read-only" note and drops the live-only links. The theme toggle cycles system / light / dark with a monitor, sun or moon icon and the word, which hides below `sm`.

### Tables and Lists
Rows are separated by hairlines, hover with the hover wash and show the J/K-active row in selection slate. Column headers are 12px medium ink-2. A key-hint row under each list shows the shortcuts as kbd chips.

### Verdict Mark
A 14px status icon (down-right arrow worse, up-right arrow better, minus not significant, equals no change, ban incomparable) in its mark colour, followed by the word in ink-2. In compact cells the word becomes screen-reader-only.

### Interval Strip
The signature mark for a delta: a 128 x 18px SVG with a strong-hairline track, an ink-2 zero tick in the centre and a shared scale for every row of a table. A bootstrap interval is an 8px band; a significant one is filled with its status colour, a non-significant one is hatched grey with a grey outline. The point is a 4px-radius dot, filled when significant and hollow grey when not. A McNemar result has no band and carries its p-value in 11px beside the point.

### Case Strip
The signature overview of a run or comparison: one 8 x 16px cell per case in case order with 2px gaps, each a link to its case (out of the tab order; the table below is the keyboard path). Pass is strong-hairline grey, miss / regressed is filled orange, fixed is filled blue, lower / higher are washes with a 1px inset ring, errored and never-generated are hatched. Cells stretch vertically 125% on hover. A legend of counts in 12px ink-2 follows.

### Triage Dialog
A 520px raised panel at 12vh from the top, with a hairline header and footer. The four categories are a two-column grid of bordered options with their single-key kbd hint; the chosen one takes an ink border on the selection slate. The footer shows the save shortcut on the left and Cancel / Save on the right.

## Do's and Don'ts

### Do:
- **Do** pair every status with an icon and a word, and give errored or never-generated items the hatch texture.
- **Do** draw doubt: hollow points and hatched grey bands for non-significant results, on the shared zero-centred scale.
- **Do** keep metric values, deltas and verdict words in ink or ink-2, right-aligned with tabular figures.
- **Do** use the slate `select` and `focus` tokens for selection, active rows and the focus ring.
- **Do** fold run status into the run marks and show it only when a run is not complete.
- **Do** make a wide table's first column sticky and fade its right edge while more columns sit off-screen.
- **Do** define every new colour for both themes and check worse / better against each other for colour-vision deficiency.

### Don't:
- **Don't** colour a number, a heading, a link or a selected row with the worse / better hues.
- **Don't** use red and green for regressed and improved.
- **Don't** load a web font, image or any network asset; the exported file must stand alone.
- **Don't** put a shadow on a section, table or button; shadows are for the dialog and tooltips.
- **Don't** make a non-significant result look like a win or a loss: no filled band, no status colour.
- **Don't** add a status column to run tables.
