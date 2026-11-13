import { Link, useNavigate } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import type { CaseRow } from '@/client/types.gen'
import { cn } from '@/lib/cn'
import { metric } from '@/lib/format'
import { useListKeys } from '@/lib/keys'
import { categoryWord } from './annotate'
import { CELL, CaseStrip, type CaseKind } from './marks'
import { KeyHints, Pager, Scroll } from './ui'

const PAGE = 100

export function cellKind(row: Pick<CaseRow, 'miss' | 'error'>): CaseKind {
  if (row.error === 'not_generated') return 'not_generated'
  if (row.error) return 'error'
  return row.miss ? 'miss' : 'pass'
}

/** A case's outcome: the strip's swatch and word (one encoding, never colour alone). */
export function Outcome({ row }: { row: Pick<CaseRow, 'miss' | 'error'> }) {
  const kind = cellKind(row)
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span aria-hidden className={cn('inline-block h-3 w-2 rounded-[1px]', CELL[kind].cls)} />
      <span className={kind === 'pass' ? 'text-ink-2' : 'text-ink'}>
        {kind === 'error' ? `error: ${row.error}` : CELL[kind].word}
      </span>
    </span>
  )
}

export function Tags({ tags }: { tags: Record<string, string> | undefined }) {
  return (
    <>
      {Object.entries(tags ?? {}).map(([k, v]) => (
        <span key={k} className="mr-2 whitespace-nowrap">
          {k}={v}
        </span>
      ))}
    </>
  )
}

/** A run's cases: the case strip over a table (J/K, Enter opens), misses filterable. */
export function CaseTable({ suite, runId, rows }: { suite: string; runId: string; rows: CaseRow[] }) {
  const navigate = useNavigate()
  const [missesOnly, setMissesOnly] = useState(false)
  const [offset, setOffset] = useState(0)
  const { cells, scorers, misses } = useMemo(
    () => ({
      cells: rows.map((r) => ({ id: r.case_id, kind: cellKind(r) })),
      scorers: [...new Set(rows.flatMap((r) => Object.keys(r.scores)))].sort(),
      misses: rows.filter((r) => r.miss).length,
    }),
    [rows],
  )
  const page = useMemo(
    () => (missesOnly ? rows.filter((r) => r.miss) : rows).slice(offset, offset + PAGE),
    [rows, missesOnly, offset],
  )
  const { list, rowProps } = useListKeys<HTMLTableSectionElement>(page.length, (i) => {
    const r = page[i]
    if (r) void navigate({ to: '/runs/$suite/$runId/cases/$caseId', params: { suite, runId, caseId: r.case_id } })
  })

  return (
    <>
      <CaseStrip suite={suite} runId={runId} label={`${rows.length} cases, in case order`} cells={cells} />
      <div className="flex items-center gap-3 border-y border-rule px-3 py-1.5 text-ink-2">
        <label className="inline-flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={missesOnly}
            onChange={(e) => {
              setMissesOnly(e.target.checked)
              setOffset(0)
            }}
            className="size-3.5 accent-[var(--focus)]"
          />
          Misses only ({misses})
        </label>
      </div>
      <Scroll>
        <table className="w-full border-collapse text-left">
          <thead className="text-[12px] text-ink-2">
            <tr className="border-b border-rule">
              <th className="sticky left-0 z-10 bg-surface px-3 py-1.5 font-medium">Case</th>
              <th className="px-2 py-1.5 font-medium">Tags</th>
              <th className="px-2 py-1.5 font-medium">Outcome</th>
              {scorers.map((s) => (
                <th key={s} className="px-2 py-1.5 text-right font-medium">
                  {s}
                </th>
              ))}
              <th className="px-3 py-1.5 font-medium">Triage</th>
            </tr>
          </thead>
          <tbody ref={list}>
            {page.map((r, i) => (
              <tr key={r.case_id} {...rowProps(i)}>
                <td className="sticky left-0 z-10 bg-inherit px-3 py-1.5 font-mono text-[12px]">
                  <Link
                    to="/runs/$suite/$runId/cases/$caseId"
                    params={{ suite, runId, caseId: r.case_id }}
                    className="text-ink hover:underline"
                  >
                    {r.case_id}
                  </Link>
                </td>
                <td className="px-2 py-1.5 text-[12px] text-ink-2">
                  <Tags tags={r.tags} />
                </td>
                <td className="px-2 py-1.5">
                  <Outcome row={r} />
                </td>
                {scorers.map((s) => (
                  <td key={s} className="px-2 py-1.5 text-right">
                    {metric(r.scores[s])}
                  </td>
                ))}
                <td className="px-3 py-1.5 text-ink-2">
                  {r.annotation ? (
                    categoryWord(r.annotation.category)
                  ) : r.older_annotation ? (
                    <span title="Triaged before the case's content changed">older version triaged</span>
                  ) : (
                    <span className="text-muted">{r.miss ? 'untriaged' : '—'}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Scroll>
      <Pager offset={offset} limit={PAGE} total={missesOnly ? misses : rows.length} onChange={setOffset} />
      <KeyHints hints={[['J K', 'move'], ['Enter', 'open']]} />
    </>
  )
}
