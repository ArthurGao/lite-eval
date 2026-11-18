import { useQuery } from '@tanstack/react-query'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useCallback, useMemo, useRef, useState } from 'react'
import { TrendChart, trendEvents } from '@/components/trend'
import { RunLink, RunMarks } from '@/components/marks'
import { Empty, ErrorPanel, Loaded, Scroll, Section, Select, Skeleton } from '@/components/ui'
import { useData } from '@/data/context'
import { allRunsQuery, trendQuery } from '@/data/queries'
import { metric, shortSha, when } from '@/lib/format'

type Search = { suite?: string; metric?: string; config?: string }

export const Route = createFileRoute('/trends')({
  validateSearch: (s: Record<string, unknown>): Search => ({
    suite: typeof s.suite === 'string' ? s.suite : undefined,
    metric: typeof s.metric === 'string' ? s.metric : undefined,
    config: typeof s.config === 'string' ? s.config : undefined,
  }),
  component: TrendsPage,
})

/** The element's width, tracked from the moment it mounts (it appears after loading). */
function useWidth() {
  const [width, setWidth] = useState(800)
  const observer = useRef<ResizeObserver | null>(null)
  const ref = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect()
    if (!el) return
    observer.current = new ResizeObserver(([entry]) => entry && setWidth(Math.floor(entry.contentRect.width)))
    observer.current.observe(el)
  }, [])
  return { ref, width }
}

function TrendsPage() {
  const search = Route.useSearch()
  const navigate = useNavigate({ from: '/trends' })
  const source = useData()
  const runs = useQuery(allRunsQuery(source))
  const items = useMemo(() => runs.data?.items ?? [], [runs.data])
  const suites = useMemo(() => [...new Set(items.map((r) => r.suite))].sort(), [items])
  const suite = search.suite ?? suites[0]
  const { metrics, configs } = useMemo(() => {
    const ofSuite = items.filter((r) => r.suite === suite)
    return {
      metrics: [...new Set(ofSuite.flatMap((r) => Object.keys(r.metrics)))].sort(),
      configs: [...new Set(ofSuite.map((r) => r.config))].sort(),
    }
  }, [items, suite])
  const chosen = search.metric && metrics.includes(search.metric) ? search.metric : metrics[0]
  const config = search.config && configs.includes(search.config) ? search.config : configs[0]
  const trend = useQuery(trendQuery(source, suite, chosen, config))
  const { ref, width } = useWidth()
  const set = (patch: Search) => void navigate({ search: (s) => ({ ...s, ...patch }) })

  // the events at each point, computed once per trend (not once per table row)
  const marksAt = useMemo(() => {
    const at = new Map<number, string[]>()
    for (const e of trend.data ? trendEvents(trend.data) : []) at.set(e.index, [...(at.get(e.index) ?? []), e.label])
    return at
  }, [trend.data])

  if (runs.isPending) return <Skeleton />
  if (runs.isError) return <ErrorPanel error={runs.error} />
  if (items.length === 0) {
    return (
      <Section title="Trends">
        <Empty title="No runs yet">A trend appears once a suite has been run more than once.</Empty>
      </Section>
    )
  }

  return (
    <div className="space-y-4">
      <Section
        title={chosen ? `${chosen} over time` : 'Trends'}
        actions={
          <>
            <Select label="Suite" value={suite ?? ''} options={suites} onChange={(v) => set({ suite: v, metric: undefined, config: undefined })} />
            <Select label="Config" value={config ?? ''} options={configs} onChange={(v) => set({ config: v })} />
            <Select label="Metric" value={chosen ?? ''} options={metrics} onChange={(v) => set({ metric: v })} />
          </>
        }
      >
        <div ref={ref} className="px-2 py-3">
          <Loaded query={trend} rows={6}>
            {(t) =>
              t.points.length === 0 ? (
                <Empty title={`No run of ${config} reports ${chosen}`} />
              ) : (
                <>
                  <TrendChart trend={t} width={Math.max(width - 16, 320)} />
                  <p className="mt-2 px-2 text-[12px] text-ink-2">
                    One point per run of {config}, in run order. The ringed point is the suite&apos;s current baseline;
                    vertical rules mark where the baseline was set and where the dataset changed version.
                    {(t.baseline_changes ?? []).length === 0 && ' No baseline switch has been logged for this suite.'}
                  </p>
                </>
              )
            }
          </Loaded>
        </div>
      </Section>

      {trend.data && trend.data.points.length > 0 && (
        <Section title="Runs in this trend">
          <Scroll>
            <table className="w-full border-collapse text-left">
              <thead className="text-[12px] text-ink-2">
                <tr className="border-b border-rule">
                  <th className="px-3 py-1.5 font-medium">Started</th>
                  <th className="px-2 py-1.5 font-medium">Run</th>
                  <th className="px-2 py-1.5 text-right font-medium">{chosen}</th>
                  <th className="px-2 py-1.5 font-medium">Dataset</th>
                  <th className="px-2 py-1.5 font-medium">Git</th>
                  <th className="px-3 py-1.5 font-medium">Marks</th>
                </tr>
              </thead>
              <tbody>
                {trend.data.points.map((p, i) => ({ p, marks: marksAt.get(i) ?? [] })).reverse().map(({ p, marks }) => {
                  return (
                    <tr key={p.run_id} className="border-b border-rule last:border-b-0">
                      <td className="px-3 py-1.5 font-mono text-[12px]">{when(p.started_at)}</td>
                      <td className="px-2 py-1.5">
                        <RunLink suite={suite!} runId={p.run_id} config={p.config} />
                      </td>
                      <td className="px-2 py-1.5 text-right">{metric(p.value)}</td>
                      <td className="px-2 py-1.5 font-mono text-[12px]">{p.dataset_version}</td>
                      <td className="px-2 py-1.5 font-mono text-[12px] text-ink-2">{shortSha(p.git_sha)}</td>
                      <td className="px-3 py-1.5 text-ink-2">
                        <span className="inline-flex flex-wrap items-center gap-2">
                          <RunMarks row={p} />
                          {marks.join(' · ')}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </Scroll>
        </Section>
      )}
    </div>
  )
}
