import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute, useNavigate } from '@tanstack/react-router'
import { GitCompareArrows, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { RunRow } from '@/client/types.gen'
import { RunMarks } from '@/components/marks'
import { Button, Empty, ErrorPanel, KeyHints, Pager, Scroll, Section, Select, Skeleton } from '@/components/ui'
import { useData } from '@/data/context'
import { runsQuery } from '@/data/queries'
import { metric, ms, usd, when } from '@/lib/format'
import { useListKeys } from '@/lib/keys'

type Search = { suite?: string; config?: string; status?: RunRow['status']; offset?: number }

const STATUSES = ['complete', 'partial', 'failed'] as const
const LIMIT = 50

export const Route = createFileRoute('/')({
  validateSearch: (s: Record<string, unknown>): Search => ({
    suite: typeof s.suite === 'string' ? s.suite : undefined,
    config: typeof s.config === 'string' ? s.config : undefined,
    status: STATUSES.find((x) => x === s.status),
    offset: typeof s.offset === 'number' && s.offset > 0 ? s.offset : undefined,
  }),
  component: RunsPage,
})

/** Baseline first: the suite's baseline if one is picked, else the older run. */
export function comparePair(a: RunRow, b: RunRow): [RunRow, RunRow] {
  if (b.baseline && !a.baseline) return [b, a]
  if (a.baseline && !b.baseline) return [a, b]
  return a.started_at <= b.started_at ? [a, b] : [b, a]
}

function RunsPage() {
  const search = Route.useSearch()
  const navigate = useNavigate({ from: '/' })
  const source = useData()
  const offset = search.offset ?? 0
  const live = source.mode === 'live' // an export holds one or two runs: no filters
  const runs = useQuery(
    runsQuery(source, { suite: search.suite, config: search.config, status: search.status, offset, limit: LIMIT }),
  )
  const [picked, setPicked] = useState<RunRow[]>([])
  const items = useMemo(() => runs.data?.items ?? [], [runs.data])

  const toggle = (r: RunRow) =>
    setPicked((p) =>
      p.some((x) => x.run_id === r.run_id) ? p.filter((x) => x.run_id !== r.run_id) : [...p.slice(-1), r],
    )
  const { list, rowProps } = useListKeys<HTMLTableSectionElement>(
    items.length,
    (i) => {
      const r = items[i]
      if (r) void navigate({ to: '/runs/$suite/$runId', params: { suite: r.suite, runId: r.run_id } })
    },
    { x: (i) => items[i] && toggle(items[i]) },
  )
  const filter = (patch: Search) => void navigate({ search: (s) => ({ ...s, ...patch, offset: undefined }) })

  // One metric column each when every listed run reports the same metrics; else one combined column.
  const names = [...new Set(items.flatMap((r) => Object.keys(r.metrics)))].sort()
  const shared = items.length > 0 && items.every((r) => Object.keys(r.metrics).length === names.length)
  const pair = picked.length === 2 ? comparePair(picked[0]!, picked[1]!) : null

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          Runs
          {runs.data && <span className="font-normal text-ink-2">{runs.data.total}</span>}
          {(['suite', 'config', 'status'] as const).map((k) =>
            live && search[k] ? (
              <button
                key={k}
                type="button"
                onClick={() => filter({ [k]: undefined })}
                className="inline-flex h-[18px] items-center gap-1 rounded-sm bg-select px-1.5 text-[11px] font-medium text-ink-2 hover:text-ink"
                aria-label={`Remove filter ${k} ${search[k]}`}
              >
                {k}: {search[k]} <X aria-hidden className="size-3" />
              </button>
            ) : null,
          )}
        </span>
      }
      actions={
        <>
          {live && (
            <Select
              label="Status"
              value={search.status ?? ''}
              options={[{ value: '', label: 'any' }, ...STATUSES.map((s) => ({ value: s, label: s }))]}
              onChange={(v) => filter({ status: v || undefined })}
            />
          )}
          <Button
            variant="primary"
            disabled={!pair}
            title={pair ? undefined : 'Tick two runs of one dataset (X selects the current row)'}
            onClick={() =>
              pair &&
              void navigate({
                to: '/compare',
                search: { baseline: `${pair[0].suite}/${pair[0].run_id}`, candidate: `${pair[1].suite}/${pair[1].run_id}` },
              })
            }
          >
            <GitCompareArrows aria-hidden className="size-3.5" />
            Compare {picked.length}/2
          </Button>
        </>
      }
    >
      {runs.isPending ? (
        <Skeleton rows={8} />
      ) : runs.isError ? (
        <ErrorPanel error={runs.error} />
      ) : items.length === 0 ? (
        <Empty title={search.suite || search.config || search.status ? 'No run matches these filters' : 'No runs yet'}>
          {search.suite || search.config || search.status ? (
            'Remove a filter above to see more.'
          ) : (
            <>
              Run a suite with <code className="font-mono">lite-eval run evals/suites/&lt;suite&gt;.yaml</code>; each
              config writes one run under <code className="font-mono">runs/</code>, and it appears here within a few
              seconds.
            </>
          )}
        </Empty>
      ) : (
        <>
          <Scroll>
            <table className="w-full border-collapse text-left">
              <thead className="text-[12px] text-ink-2">
                <tr className="border-b border-rule">
                  <th className="sticky left-0 z-10 bg-surface px-3 py-1.5 font-medium">
                    <span className="sr-only">Pick for comparison, </span>Config
                  </th>
                  <th className="px-2 py-1.5 font-medium">Suite</th>
                  <th className="px-2 py-1.5 font-medium">Dataset</th>
                  {shared ? (
                    names.map((n) => (
                      <th key={n} className="px-2 py-1.5 text-right font-medium">
                        {n}
                      </th>
                    ))
                  ) : (
                    <th className="px-2 py-1.5 font-medium">Metrics</th>
                  )}
                  <th className="px-2 py-1.5 text-right font-medium" title="Cases, and outputs that errored">
                    Cases
                  </th>
                  <th className="px-2 py-1.5 text-right font-medium">p50</th>
                  <th className="px-2 py-1.5 text-right font-medium">Cost</th>
                  <th className="px-3 py-1.5 font-medium">Started</th>
                </tr>
              </thead>
              <tbody ref={list}>
                {items.map((r, i) => {
                  const isPicked = picked.some((x) => x.run_id === r.run_id)
                  return (
                    <tr key={`${r.suite}/${r.run_id}`} {...rowProps(i)}>
                      <td className="sticky left-0 z-10 bg-inherit px-3 py-1.5">
                        <div className="flex items-center gap-2 whitespace-nowrap">
                          <input
                            type="checkbox"
                            checked={isPicked}
                            onChange={() => toggle(r)}
                            aria-label={`Pick ${r.config} ${r.run_id} for comparison`}
                            className="size-3.5 accent-[var(--focus)]"
                          />
                          <Link
                            to="/runs/$suite/$runId"
                            params={{ suite: r.suite, runId: r.run_id }}
                            className="font-medium text-ink hover:underline"
                          >
                            {r.config}
                          </Link>
                          <RunMarks row={r} />
                        </div>
                      </td>
                      <td className="px-2 py-1.5">
                        {live ? (
                          <button type="button" className="hover:underline" onClick={() => filter({ suite: r.suite })}>
                            {r.suite}
                          </button>
                        ) : (
                          r.suite
                        )}
                      </td>
                      <td className="px-2 py-1.5 whitespace-nowrap text-ink-2">
                        {r.dataset}
                        <span className="font-mono text-[12px]">@{r.dataset_version}</span>
                      </td>
                      {shared ? (
                        names.map((n) => (
                          <td key={n} className="px-2 py-1.5 text-right">
                            {metric(r.metrics[n])}
                          </td>
                        ))
                      ) : (
                        <td className="px-2 py-1.5 text-ink-2">
                          {Object.entries(r.metrics)
                            .slice(0, 4)
                            .map(([n, v]) => (
                              <span key={n} className="mr-3 whitespace-nowrap">
                                {n} <span className="text-ink">{metric(v)}</span>
                              </span>
                            ))}
                        </td>
                      )}
                      <td className="px-2 py-1.5 text-right whitespace-nowrap">
                        {r.n_cases}
                        {r.target_errors > 0 && (
                          <span className="ml-1 text-worse-text" title="Outputs that errored">
                            · {r.target_errors} err
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-1.5 text-right text-ink-2">{ms(r.latency_p50_ms)}</td>
                      <td className="px-2 py-1.5 text-right text-ink-2">{usd(r.cost_usd)}</td>
                      <td className="px-3 py-1.5 font-mono text-[12px] whitespace-nowrap text-ink-2" title={r.started_at}>
                        {when(r.started_at)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </Scroll>
          <Pager offset={offset} limit={LIMIT} total={runs.data.total} onChange={(o) => void navigate({ search: (s) => ({ ...s, offset: o || undefined }) })} />
          <KeyHints hints={[['J K', 'move'], ['Enter', 'open'], ['X', 'pick two runs to compare them']]} />
        </>
      )}
    </Section>
  )
}
