import { Link } from '@tanstack/react-router'
import { memo } from 'react'
import type { MetricDelta, RunRow } from '@/client/types.gen'
import { cn } from '@/lib/cn'
import { delta as fmtDelta, pValue, runTag } from '@/lib/format'
import { verdict as verdictOf } from '@/lib/verdict'
import { Tag } from './ui'

/** Every doubt a run carries, on every page that lists it (frontend AGENTS rule 3). */
export function RunMarks({ row }: { row: Pick<RunRow, 'baseline' | 'status' | 'warnings' | 'synthetic'> }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      {row.baseline && <Tag title="This suite's baseline: gates and comparisons start here">baseline</Tag>}
      {row.status !== 'complete' && (
        <Tag tone="worse" title={row.status === 'partial' ? 'Stopped early (budget or interruption)' : 'No case was scored'}>
          {row.status}
        </Tag>
      )}
      {row.warnings > 0 && (
        <Tag tone="warn" title="Self-checks raised warnings; see the run's page">
          {row.warnings} {row.warnings === 1 ? 'warning' : 'warnings'}
        </Tag>
      )}
      {row.synthetic && <Tag title="The dataset is marked synthetic">synthetic</Tag>}
    </span>
  )
}

const W = 128
const H = 18

/**
 * The interval strip (signature move): a delta on a shared scale centred on a zero tick, so
 * every row reads against the same yardstick. A bootstrap interval is a band; a band that is not
 * significant is hatched grey, and a point that is not significant is hollow -- doubt is drawn,
 * not only written. A McNemar result (no interval) carries its p-value beside the point.
 * Colour sits on the mark, never on the number.
 */
export function IntervalStrip({ d, scale }: { d: MetricDelta; scale: number }) {
  const v = verdictOf(d)
  const sure = v === 'worse' || v === 'better'
  const x = (value: number) => W / 2 + (value / scale) * (W / 2 - 6)
  const tone = v === 'worse' ? 'var(--worse-mark)' : v === 'better' ? 'var(--better-mark)' : 'var(--neutral-mark)'
  const hatch = `hatch-${d.metric.replace(/[^a-zA-Z0-9]/g, '_')}`
  const band = d.ci_low != null && d.ci_high != null
  const label =
    d.delta == null
      ? 'no delta'
      : `delta ${fmtDelta(d.delta)}` +
        (band ? `, 95% interval ${fmtDelta(d.ci_low)} to ${fmtDelta(d.ci_high)}` : d.p_value != null ? `, ${pValue(d.p_value)}` : '') +
        (sure ? '' : ', not significant')
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg width={W} height={H} role="img" aria-label={label} className="shrink-0 overflow-visible">
        <title>{label}</title>
        <defs>
          <pattern id={hatch} width={4} height={4} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width={4} height={4} fill="var(--surface)" />
            <line x1={0} y1={0} x2={0} y2={4} stroke="var(--neutral-mark)" strokeWidth={2} />
          </pattern>
        </defs>
        <line x1={0} x2={W} y1={H / 2} y2={H / 2} stroke="var(--rule-strong)" strokeWidth={1} />
        <line x1={W / 2} x2={W / 2} y1={1} y2={H - 1} stroke="var(--ink-2)" strokeWidth={1} />
        {band && (
          <rect
            x={x(d.ci_low!)}
            y={H / 2 - 4}
            width={Math.max(x(d.ci_high!) - x(d.ci_low!), 2)}
            height={8}
            rx={2}
            fill={sure ? tone : `url(#${hatch})`}
            stroke={sure ? 'none' : 'var(--neutral-mark)'}
            strokeWidth={1}
          />
        )}
        {d.delta != null && (
          <circle
            cx={x(d.delta)}
            cy={H / 2}
            r={4}
            fill={sure ? tone : 'var(--surface)'}
            stroke={sure ? 'var(--surface)' : 'var(--neutral-mark)'}
            strokeWidth={2}
          />
        )}
      </svg>
      {!band && d.p_value != null && <span className="text-[11px] whitespace-nowrap text-ink-2">{pValue(d.p_value)}</span>}
    </span>
  )
}

/** A case of one run. */
export type CaseKind = 'pass' | 'miss' | 'error' | 'not_generated'
/** A case in a strip: a run's outcome, or how it changed between two runs. */
export type CellKind = CaseKind | 'regressed' | 'fixed' | 'lower' | 'higher'

export const CELL: Record<CellKind, { cls: string; word: string }> = {
  pass: { cls: 'bg-neutral-mark', word: 'pass' }, // >= 3:1 on the surface (WCAG 1.4.11)
  miss: { cls: 'bg-worse-mark', word: 'miss' },
  regressed: { cls: 'bg-worse-mark', word: 'regressed' },
  fixed: { cls: 'bg-better-mark', word: 'fixed' },
  lower: { cls: 'bg-worse-wash ring-1 ring-inset ring-worse-mark', word: 'lower' },
  higher: { cls: 'bg-better-wash ring-1 ring-inset ring-better-mark', word: 'higher' },
  error: { cls: 'hatch bg-worse-wash text-worse-mark', word: 'errored' },
  not_generated: { cls: 'hatch bg-hover text-neutral-mark', word: 'never generated' },
}

/**
 * The case strip (signature move): one cell per case, in case order, each a link to the case.
 * Errors and never-generated cases carry a texture, so the strip reads without colour too.
 * Cells are out of the tab order; the case table under it is the keyboard path.
 */
export const CaseStrip = memo(function CaseStrip({
  cells,
  suite,
  runId,
  label,
  extra,
}: {
  cells: { id: string; kind: CellKind }[]
  suite: string
  runId: string
  label: string
  extra?: string
}) {
  const counts: Partial<Record<CellKind, number>> = {}
  for (const c of cells) counts[c.kind] = (counts[c.kind] ?? 0) + 1
  return (
    <figure className="px-3 py-2.5">
      <div role="list" aria-label={label} className="flex flex-wrap gap-[2px]">
        {cells.map((c) => (
          <Link
            key={c.id}
            role="listitem"
            tabIndex={-1}
            to="/runs/$suite/$runId/cases/$caseId"
            params={{ suite, runId, caseId: c.id }}
            title={`${c.id}: ${CELL[c.kind].word}`}
            aria-label={`${c.id}: ${CELL[c.kind].word}`}
            className={cn(
              'block h-4 w-2 rounded-[1px] outline-offset-1 transition-transform duration-150 hover:scale-y-125',
              CELL[c.kind].cls,
            )}
          />
        ))}
      </div>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-2">
        {(Object.keys(CELL) as CellKind[])
          .filter((k) => counts[k])
          .map((k) => (
            <span key={k} className="inline-flex items-center gap-1.5">
              <span aria-hidden className={cn('inline-block h-3 w-2 rounded-[1px]', CELL[k].cls)} />
              {counts[k]} {CELL[k].word}
            </span>
          ))}
        {extra && <span>{extra}</span>}
      </figcaption>
    </figure>
  )
})

/** A run as "config <run tag>", linking to its page. */
export function RunLink({ suite, runId, config, caseId }: { suite: string; runId: string; config: string; caseId?: string }) {
  const tag = <span className="font-mono text-[12px] font-normal text-ink-2">{runTag(runId)}</span>
  return caseId ? (
    <Link to="/runs/$suite/$runId/cases/$caseId" params={{ suite, runId, caseId }} className="hover:underline">
      {config} {tag}
    </Link>
  ) : (
    <Link to="/runs/$suite/$runId" params={{ suite, runId }} className="hover:underline">
      {config} {tag}
    </Link>
  )
}
