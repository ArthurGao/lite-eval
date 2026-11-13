import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { GitCompareArrows, ListChecks } from 'lucide-react'
import type { RunRecord } from '@/client/types.gen'
import { CaseTable } from '@/components/cases'
import { RunMarks } from '@/components/marks'
import { Empty, ErrorPanel, Scroll, Section, Skeleton, WarningsList, buttonClass } from '@/components/ui'
import { useData } from '@/data/context'
import { casesQuery, runQuery, runsQuery } from '@/data/queries'
import { configName, metric, ms, shortHash, shortSha, usd, weight, when } from '@/lib/format'

export const Route = createFileRoute('/runs/$suite/$runId/')({ component: RunPage })

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[12px] text-ink-2">{label}</dt>
      <dd className="truncate text-ink">{children}</dd>
    </div>
  )
}

function RunPage() {
  const { suite, runId } = Route.useParams()
  const source = useData()
  const run = useQuery(runQuery(source, suite, runId))
  const cases = useQuery(casesQuery(source, suite, runId))
  const baseline = useQuery({ ...runsQuery(source, { suite, baseline: true, limit: 1 }), enabled: source.mode === 'live' })

  if (run.isPending) return <Skeleton rows={10} />
  if (run.isError) return <ErrorPanel error={run.error} />
  const rec = run.data.record
  const base = baseline.data?.items[0]
  const misses = cases.data?.items.filter((r) => r.miss).length ?? 0

  return (
    <div className="space-y-4">
      <RunHeader record={rec} baseline={run.data.baseline} />
      <div className="flex flex-wrap gap-2">
        {misses > 0 && (
          <Link
            to="/runs/$suite/$runId/triage"
            params={{ suite, runId }}
            className={buttonClass('primary')}
          >
            <ListChecks aria-hidden className="size-3.5" />
            Triage {misses} {misses === 1 ? 'miss' : 'misses'}
          </Link>
        )}
        {base && base.run_id !== runId && (
          <Link
            to="/compare"
            search={{ baseline: `${suite}/${base.run_id}`, candidate: `${suite}/${runId}` }}
            className={buttonClass('quiet')}
          >
            <GitCompareArrows aria-hidden className="size-3.5" />
            Compare with baseline ({base.config})
          </Link>
        )}
      </div>

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Metrics record={rec} />
        <Costs record={rec} />
      </div>

      <WarningsList warnings={rec.warnings ?? []} />

      <Section title={`Cases (${rec.summary.n_cases})`}>
        {cases.isPending ? (
          <Skeleton />
        ) : cases.isError ? (
          <ErrorPanel error={cases.error} />
        ) : cases.data.items.length === 0 ? (
          <Empty title="This run has no case results" />
        ) : (
          <CaseTable suite={suite} runId={runId} rows={cases.data.items} />
        )}
      </Section>

      <details className="rounded border border-rule bg-surface">
        <summary className="cursor-pointer px-3 py-2 font-semibold">Configuration and versions</summary>
        <pre className="max-h-[480px] overflow-auto border-t border-rule px-3 py-2 font-mono text-[12px] text-ink-2">
          {JSON.stringify(
            { target: rec.target, scorers: rec.scorers, dataset: rec.dataset, repeats: rec.repeats, seed: rec.seed, redact: rec.redact, gate: rec.gate, ci: rec.ci, cache: rec.cache },
            null,
            2,
          )}
        </pre>
      </details>
    </div>
  )
}

function RunHeader({ record: r, baseline }: { record: RunRecord; baseline: boolean }) {
  const config = configName(r)
  return (
    <header className="rounded border border-rule bg-surface px-3 py-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-[16px] font-semibold text-ink">
          <Link to="/" search={{ suite: r.suite.name }} className="font-normal text-ink-2 hover:underline">
            {r.suite.name}
          </Link>
          <span className="mx-1.5 text-muted">/</span>
          {config}
        </h1>
        <span className="font-mono text-[12px] text-ink-2">{r.run_id}</span>
        <RunMarks
          row={{
            baseline,
            status: r.status,
            warnings: r.warnings?.length ?? 0,
            synthetic: r.dataset.synthetic ?? false,
          }}
        />
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3 lg:grid-cols-6">
        <Fact label="Dataset">
          {r.dataset.name}
          <span className="font-mono text-[12px] text-ink-2">@{r.dataset.version}</span>
        </Fact>
        <Fact label="Dataset hash">
          <span className="font-mono text-[12px]" title={r.dataset.hash}>{shortHash(r.dataset.hash)}</span>
        </Fact>
        <Fact label="Target">{r.target.name}</Fact>
        <Fact label="Started">
          <span className="font-mono text-[12px]" title={r.started_at}>{when(r.started_at)}</span>
        </Fact>
        <Fact label="Git">
          <span className="font-mono text-[12px]">
            {shortSha(r.git_sha)}
            {r.git_dirty ? ' (dirty)' : ''}
          </span>
        </Fact>
        <Fact label="lite-eval">{r.lite_eval_version}</Fact>
      </dl>
    </header>
  )
}

function Metrics({ record: r }: { record: RunRecord }) {
  const s = r.summary
  const metrics = Object.entries(s.metrics ?? {})
  const groups = Object.entries(s.groups ?? {})
  return (
    <Section
      title="Metrics"
      actions={
        <span className="text-[12px] text-ink-2" title="How much a single case moves a 0..1 metric">
          1 case = {weight(s.case_weight)} · {s.n_cases} cases × {s.repeats} {s.repeats === 1 ? 'repeat' : 'repeats'}
        </span>
      }
    >
      <table className="w-full border-collapse text-left">
        <thead className="text-[12px] text-ink-2">
          <tr className="border-b border-rule">
            <th className="px-3 py-1.5 font-medium">Metric</th>
            <th className="px-2 py-1.5 text-right font-medium">Value</th>
            {s.repeats > 1 && (
              <th className="px-3 py-1.5 text-right font-medium" title="The metric computed per repeat">
                Min – max over repeats
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {metrics.map(([name, m]) => (
            <tr key={name} className="border-b border-rule last:border-b-0">
              <td className="px-3 py-1.5">{name}</td>
              <td className="px-2 py-1.5 text-right font-medium">{metric(m.value)}</td>
              {s.repeats > 1 && (
                <td className="px-3 py-1.5 text-right text-ink-2">
                  {metric(m.min)} – {metric(m.max)}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {groups.map(([tag, byValue]) => {
        const values = Object.entries(byValue)
        const names = [...new Set(values.flatMap(([, v]) => Object.keys(v)))].sort()
        return (
          <div key={tag} className="border-t border-rule">
            <Scroll>
              <table className="w-full border-collapse text-left">
                <caption className="px-3 pt-2 pb-1 text-left text-[12px] font-semibold text-ink">By {tag}</caption>
                <thead className="text-[12px] text-ink-2">
                  <tr className="border-y border-rule bg-hover">
                    <th className="px-3 py-1 font-medium">{tag}</th>
                    {names.map((n) => (
                      <th key={n} className="px-2 py-1 text-right font-medium">
                        {n}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {values.map(([value, ms]) => (
                    <tr key={value}>
                      <td className="px-3 py-1">{value}</td>
                      {names.map((n) => (
                        <td key={n} className="px-2 py-1 text-right">
                          {metric(ms[n])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </Scroll>
          </div>
        )
      })}
    </Section>
  )
}

function Costs({ record: r }: { record: RunRecord }) {
  const c = r.cost
  const s = r.summary
  const errors = Object.entries(s.target_errors ?? {})
  const scoring = Object.entries(s.scoring_errors ?? {})
  const scope = (r.target.info as Record<string, string> | undefined)?.latency_scope
  return (
    <Section title="Cost, latency and errors">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 px-3 py-2.5">
        <Fact label="Calls">{c.calls ?? 0}</Fact>
        <Fact label="Cost">{usd(c.cost_usd ?? 0)}</Fact>
        <Fact label="Tokens in / out">
          {c.input_tokens ?? 0} / {c.output_tokens ?? 0}
          {(c.reasoning_tokens ?? 0) > 0 && <span className="text-ink-2"> (reasoning {c.reasoning_tokens})</span>}
        </Fact>
        <Fact label="Usage unknown">{c.usage_unknown ?? 0} outputs</Fact>
        <Fact label="Latency p50">{ms(c.latency_p50_ms)}</Fact>
        <Fact label="Latency p95">{ms(c.latency_p95_ms)}</Fact>
        <Fact label="Outputs recorded">
          {s.outputs_recorded} / {s.outputs_expected}
        </Fact>
        <Fact label="Cache">
          {r.cache?.hits ?? 0} hits · {r.cache?.misses ?? 0} generated
        </Fact>
      </dl>
      {scope && <p className="border-t border-rule px-3 py-2 text-[12px] text-ink-2">Latency covers: {scope}</p>}
      <div className="border-t border-rule px-3 py-2">
        {errors.length === 0 && scoring.length === 0 ? (
          <p className="text-ink-2">No target or scoring errors.</p>
        ) : (
          <ul className="space-y-0.5">
            {errors.map(([k, n]) => (
              <li key={k}>
                <span className="text-worse-text">{n}</span> target {k}
              </li>
            ))}
            {scoring.map(([k, n]) => (
              <li key={k}>
                <span className="text-worse-text">{n}</span> scoring {k.replaceAll('_', ' ')}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  )
}
