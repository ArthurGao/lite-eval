import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import type { CaseChange, Comparison, MetricDelta, RunRow } from '@/client/types.gen'
import { CaseStrip, IntervalStrip, RunLink } from '@/components/marks'
import { Button, Empty, KeyHints, Loaded, Scroll, Section, Select, Tag, VerdictMark, WarningsList } from '@/components/ui'
import { useData } from '@/data/context'
import { allRunsQuery, compareQuery } from '@/data/queries'
import { delta as fmtDelta, metric, pValue, weight, when } from '@/lib/format'
import { useListKeys } from '@/lib/keys'
import { intervalDomain, verdict } from '@/lib/verdict'

type Search = { baseline?: string; candidate?: string }

export const Route = createFileRoute('/compare')({
  validateSearch: (s: Record<string, unknown>): Search => ({
    baseline: typeof s.baseline === 'string' ? s.baseline : undefined,
    candidate: typeof s.candidate === 'string' ? s.candidate : undefined,
  }),
  component: ComparePage,
})

const split = (ref: string) => {
  const i = ref.indexOf('/')
  return { suite: ref.slice(0, i), runId: ref.slice(i + 1) }
}

function ComparePage() {
  const { baseline, candidate } = Route.useSearch()
  if (!baseline || !candidate) return <Picker />
  return <Result baseline={baseline} candidate={candidate} />
}

function Picker() {
  const source = useData()
  const navigate = useNavigate({ from: '/compare' })
  const runs = useQuery(allRunsQuery(source))
  const [a, setA] = useState('')
  const [b, setB] = useState('')

  return (
    <Loaded query={runs}>
      {(data) => {
        const items = data.items
        const ref = (r: RunRow) => `${r.suite}/${r.run_id}`
        const first = items.find((r) => ref(r) === a)
        const options = (filter?: RunRow) => [
          { value: '', label: 'choose a run' },
          ...items
            .filter((r) => !filter || (r.dataset === filter.dataset && ref(r) !== ref(filter)))
            .map((r) => ({
              value: ref(r),
              label: `${r.suite} / ${r.config} · ${when(r.started_at)}${r.baseline ? ' · baseline' : ''}`,
            })),
        ]

        return (
          <Section title="Compare two runs">
            {items.length < 2 ? (
              <Empty title="Two runs of one dataset are needed">Run a suite with two configs, or run it again after a change.</Empty>
            ) : (
              <form
                className="flex flex-wrap items-end gap-3 px-3 py-3"
                onSubmit={(e) => {
                  e.preventDefault()
                  if (a && b) void navigate({ search: { baseline: a, candidate: b } })
                }}
              >
                <Select
                  label="Baseline"
                  value={a}
                  options={options()}
                  onChange={(v) => {
                    setA(v)
                    setB('')
                  }}
                  className="min-w-[22rem]"
                />
                <Select
                  label="Candidate (same dataset)"
                  value={b}
                  options={first ? options(first) : options().slice(0, 1)}
                  onChange={setB}
                  disabled={!first}
                  className="min-w-[22rem]"
                />
                <Button type="submit" variant="primary" disabled={!a || !b}>
                  Compare
                </Button>
              </form>
            )}
          </Section>
        )
      }}
    </Loaded>
  )
}

function Result({ baseline, candidate }: { baseline: string; candidate: string }) {
  const source = useData()
  const comparison = useQuery(compareQuery(source, baseline, candidate))
  const a = split(baseline)
  const b = split(candidate)

  return (
    <Loaded query={comparison} rows={10}>
      {(c) => (
      <div className="space-y-4">
        <header className="rounded border border-rule bg-surface px-3 py-2.5">
          <h1 className="flex flex-wrap items-baseline gap-x-2 text-[16px] font-semibold">
            <RunLink suite={a.suite} runId={a.runId} config={c.baseline_config || a.runId} />
            <span className="font-normal text-ink-2">→</span>
            <RunLink suite={b.suite} runId={b.runId} config={c.candidate_config || b.runId} />
          </h1>
          <p className="mt-1 text-ink-2">
            Paired on {c.dataset}: {c.metrics[0]?.n ?? 0} cases compared · 1 case = {weight(c.case_weight)}
            {c.overlap === 'partial_overlap' && (
              <span className="ml-2">
                <Tag tone="warn" title="Only cases whose content is the same in both runs are compared">
                  partial overlap
                </Tag>{' '}
                {c.changed_cases?.length ?? 0} changed · {c.added_cases?.length ?? 0} added · {c.removed_cases?.length ?? 0} removed,
                left out
              </span>
            )}
          </p>
        </header>

        <MetricsTable metrics={c.metrics} />

        <WarningsList warnings={c.warnings ?? []} />

        <Changes comparison={c} suite={b.suite} runId={b.runId} />
      </div>
      )}
    </Loaded>
  )
}


/** Deltas with their doubt (signature move): value, interval strip, verdict, test, n. */
export function MetricsTable({ metrics }: { metrics: MetricDelta[] }) {
  const scale = intervalDomain(metrics.filter((m) => m.status === 'compared'))
  return (
    <Section title="Metrics">
      <Scroll>
        <table className="w-full border-collapse text-left">
          <thead className="text-[12px] text-ink-2">
            <tr className="border-b border-rule">
              <th className="sticky left-0 z-10 bg-surface px-3 py-1.5 font-medium">Metric</th>
              <th className="px-2 py-1.5 text-right font-medium">Delta</th>
              <th className="px-2 py-1.5 font-medium">Verdict</th>
              <th className="px-2 py-1.5 font-medium">
                <span title="Every row's delta on one scale, zero tick in the middle. A band is the 95% bootstrap interval; hatched and hollow mean not significant.">
                  Interval <span className="font-normal whitespace-nowrap">(scale ±{scale.toFixed(2)})</span>
                </span>
              </th>
              <th className="px-2 py-1.5 text-right font-medium">Baseline</th>
              <th className="px-2 py-1.5 text-right font-medium">Candidate</th>
              <th className="px-3 py-1.5 font-medium">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((m) => {
              const v = verdict(m)
              return (
                <tr key={m.metric} className="border-b border-rule bg-surface last:border-b-0">
                  <td className="sticky left-0 z-10 bg-surface px-3 py-1.5 font-medium">{m.metric}</td>
                  {m.status === 'incomparable' ? (
                    <td colSpan={6} className="px-2 py-1.5 text-ink-2">
                      <VerdictMark verdict={v} /> <span className="ml-2">{m.reason}</span>
                    </td>
                  ) : (
                    <>
                      <td className="px-2 py-1.5 text-right font-medium whitespace-nowrap">{fmtDelta(m.delta)}</td>
                      <td className="px-2 py-1.5 whitespace-nowrap" title={m.note || undefined}>
                        <VerdictMark verdict={v} />
                        {m.within_noise && (
                          <span className="ml-2 text-[12px] text-ink-2" title="Smaller than the spread between repeats of one run">
                            within noise
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        <IntervalStrip d={m} scale={scale} />
                      </td>
                      <td className="px-2 py-1.5 text-right">{metric(m.baseline)}</td>
                      <td className="px-2 py-1.5 text-right">{metric(m.candidate)}</td>
                      <td className="px-3 py-1.5 text-[12px] whitespace-nowrap text-ink-2">
                        {m.test === 'mcnemar' ? 'McNemar' : m.test === 'bootstrap' ? 'bootstrap' : ''}
                        {m.test === 'mcnemar' && ` · ${pValue(m.p_value)}`}
                        {m.test === 'bootstrap' && m.ci_low != null && ` · 95% [${fmtDelta(m.ci_low)}, ${fmtDelta(m.ci_high)}]`}
                        {` · n=${m.n ?? 0}, ${m.discordant ?? 0} differ`}
                      </td>
                    </>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </Scroll>
    </Section>
  )
}

/**
 * How a case moved, in words: a pass/fail flip is regressed / fixed, a continuous value lower /
 * higher. Which way is worse comes from the API (`direction`), never from the browser.
 */
export function word(x: CaseChange): 'regressed' | 'fixed' | 'lower' | 'higher' {
  if (x.kind !== 'changed') return x.kind
  return x.direction === 'worse' ? 'lower' : 'higher'
}

/** Per-case changes, regressions first (the API's order; frontend AGENTS rule 4). */
function Changes({ comparison: c, suite, runId }: { comparison: Comparison; suite: string; runId: string }) {
  const navigate = useNavigate()
  const changed = new Set(c.changes.map((x) => x.metric))
  const metricsList = c.metrics.map((m) => m.metric).filter((m) => changed.has(m))
  const [only, setOnly] = useState<string>(metricsList[0] ?? '')
  const changes = useMemo(() => c.changes.filter((x) => !only || x.metric === only), [c.changes, only])
  const cells = useMemo(() => changes.map((x) => ({ id: x.case_id, kind: word(x) })), [changes])
  const { list, rowProps } = useListKeys<HTMLTableSectionElement>(changes.length, (i) => {
    const x = changes[i]
    if (x) void navigate({ to: '/runs/$suite/$runId/cases/$caseId', params: { suite, runId, caseId: x.case_id } })
  })
  const compared = c.metrics.find((m) => m.metric === only)?.n ?? 0
  const casesChanged = new Set(changes.map((x) => x.case_id)).size

  return (
    <Section
      title={`Cases that changed (${changes.length})`}
      actions={
        metricsList.length > 1 && (
          <Select
            label="Metric"
            value={only}
            options={[...metricsList.map((m) => ({ value: m, label: m })), { value: '', label: 'all metrics' }]}
            onChange={setOnly}
          />
        )
      }
    >
      {changes.length === 0 ? (
        <Empty title="No case changed">Every compared case scored the same in both runs.</Empty>
      ) : (
        <>
          <CaseStrip
            suite={suite}
            runId={runId}
            label={`Changed cases${only ? ` for ${only}` : ''}, regressions first`}
            cells={cells}
            extra={only ? `${Math.max(compared - casesChanged, 0)} unchanged` : undefined}
          />
          <Scroll>
            <table className="w-full border-collapse border-t border-rule text-left">
              <thead className="text-[12px] text-ink-2">
                <tr className="border-b border-rule">
                  <th className="sticky left-0 z-10 bg-surface px-3 py-1.5 font-medium">Case</th>
                  <th className="px-2 py-1.5 font-medium">Change</th>
                  <th className="px-2 py-1.5 text-right font-medium">Baseline</th>
                  <th className="px-2 py-1.5 text-right font-medium">Candidate</th>
                  <th className="px-3 py-1.5 font-medium">Metric</th>
                </tr>
              </thead>
              <tbody ref={list}>
                {changes.map((x, i) => (
                  <tr key={`${x.case_id}:${x.metric}`} {...rowProps(i)}>
                    <td className="sticky left-0 z-10 bg-inherit px-3 py-1.5 font-mono text-[12px]">
                      <Link to="/runs/$suite/$runId/cases/$caseId" params={{ suite, runId, caseId: x.case_id }} className="hover:underline">
                        {x.case_id}
                      </Link>
                    </td>
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      <VerdictMark verdict={x.direction === 'worse' ? 'worse' : 'better'} compact />
                      <span className="ml-1">{word(x)}</span>
                    </td>
                    <td className="px-2 py-1.5 text-right">{metric(x.baseline)}</td>
                    <td className="px-2 py-1.5 text-right">{metric(x.candidate)}</td>
                    <td className="px-3 py-1.5 text-ink-2">{x.metric}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Scroll>
          <KeyHints hints={[['J K', 'move'], ['Enter', "open the candidate's case"]]} />
        </>
      )}
    </Section>
  )
}
