import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute, useNavigate } from '@tanstack/react-router'
import { ChevronLeft, ChevronRight, Tag as TagIcon } from 'lucide-react'
import { useState } from 'react'
import { AnnotateDialog, categoryWord } from '@/components/annotate'
import { Outcome } from '@/components/cases'
import { ContentNote, OutputBody, ScoreCard, SideBySide, TraceTimeline, Value } from '@/components/detail'
import { RunLink } from '@/components/marks'
import { Button, Empty, ErrorPanel, Kbd, Section, Skeleton, Tag, buttonClass } from '@/components/ui'
import { useData } from '@/data/context'
import { metric, when } from '@/lib/format'
import { useShortcuts } from '@/lib/keys'
import { caseQuery } from '@/data/queries'

export const Route = createFileRoute('/runs/$suite/$runId/cases/$caseId')({ component: CasePage })

function CasePage() {
  const { suite, runId, caseId } = Route.useParams()
  const source = useData()
  const navigate = useNavigate()
  const detail = useQuery(caseQuery(source, suite, runId, caseId))
  const [repeat, setRepeat] = useState(0)
  const [triaging, setTriaging] = useState(false)
  const prev = detail.data?.prev_case_id ?? null
  const next = detail.data?.next_case_id ?? null
  const go = (id: string | null) =>
    id && void navigate({ to: '/runs/$suite/$runId/cases/$caseId', params: { suite, runId, caseId: id } })
  useShortcuts({ '[': () => go(prev), ']': () => go(next), t: () => source.mode === 'live' && setTriaging(true) })

  if (detail.isPending) return <Skeleton rows={12} />
  if (detail.isError) return <ErrorPanel error={detail.error} />
  const d = detail.data
  const outputs = d.outputs ?? []
  const repeats = Math.max(outputs.length, 1)
  const output = outputs.find((o) => (o.repeat ?? 0) === repeat) ?? outputs[0]
  const scores = (d.scores ?? []).filter((s) => (s.repeat ?? 0) === (output?.repeat ?? repeat))
  const content = d.content

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded border border-rule bg-surface px-3 py-2.5">
        <nav aria-label="Breadcrumb" className="text-ink-2">
          <Link to="/runs/$suite/$runId" params={{ suite, runId }} className="hover:underline">
            {suite} / <span className="font-mono text-[12px]">{runId}</span>
          </Link>
        </nav>
        <h1 className="font-mono text-[15px] font-semibold text-ink">{d.case_id}</h1>
        <Outcome row={d} />
        {d.annotation && <Tag>{categoryWord(d.annotation.category)}</Tag>}
        <div className="ml-auto flex items-center gap-2">
          {source.mode === 'live' && (
            <Button variant="primary" onClick={() => setTriaging(true)}>
              <TagIcon aria-hidden className="size-3.5" />
              {d.annotation ? 'Correct triage' : 'Triage'} <Kbd>T</Kbd>
            </Button>
          )}
          <Link
            to="/runs/$suite/$runId/cases/$caseId"
            params={{ suite, runId, caseId: prev ?? caseId }}
            disabled={!prev}
            aria-label="Previous case"
            className={buttonClass('quiet', 'px-1.5')}
          >
            <ChevronLeft aria-hidden className="size-4" />
          </Link>
          <Link
            to="/runs/$suite/$runId/cases/$caseId"
            params={{ suite, runId, caseId: next ?? caseId }}
            disabled={!next}
            aria-label="Next case"
            className={buttonClass('quiet', 'px-1.5')}
          >
            <ChevronRight aria-hidden className="size-4" />
          </Link>
          <span className="text-[12px] whitespace-nowrap text-ink-2">
            <Kbd>[</Kbd> <Kbd>]</Kbd> previous / next case
          </span>
        </div>
      </header>

      <ContentNote status={content.status} />

      {repeats > 1 && (
        <div role="tablist" aria-label="Repeat" className="flex gap-1">
          {outputs.map((o) => (
            <button
              key={o.repeat ?? 0}
              type="button"
              role="tab"
              aria-selected={(o.repeat ?? 0) === (output?.repeat ?? 0)}
              onClick={() => setRepeat(o.repeat ?? 0)}
              className="h-7 rounded border border-rule-strong px-2 aria-selected:border-ink aria-selected:bg-select"
            >
              repeat {o.repeat ?? 0}
            </button>
          ))}
        </div>
      )}

      <SideBySide
        panes={[
          { title: 'Input', body: content.case ? <Value value={content.case.input} /> : <Missing /> },
          { title: 'Expected', body: content.case ? <Value value={content.case.expected} /> : <Missing /> },
          {
            title: 'Output',
            note: repeats > 1 ? `repeat ${output?.repeat ?? 0}` : undefined,
            body: <OutputBody output={output} />,
          },
        ]}
      />

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="Scores">
          {scores.length === 0 ? <Empty title="No scores for this output" /> : scores.map((s) => <ScoreCard key={s.scorer} score={s} />)}
        </Section>
        <Section title="Trace">{output ? <TraceTimeline trace={output.trace ?? {}} /> : <Missing />}</Section>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="Triage">
          <div className="space-y-2 px-3 py-2.5">
            {d.annotation ? (
              <p>
                <span className="font-medium">{categoryWord(d.annotation.category)}</span>
                <span className="text-ink-2">
                  {' '}
                  by {d.annotation.author} · <span className="font-mono text-[12px]">{when(d.annotation.created_at)}</span>
                </span>
                {d.annotation.note && <span className="mt-1 block text-ink">{d.annotation.note}</span>}
              </p>
            ) : (
              <p className="text-ink-2">Not triaged for this content.</p>
            )}
            {(d.older_annotations ?? []).map((a) => (
              <p key={a.annotation_id} className="text-[12px] text-ink-2">
                Earlier content of this case was triaged {categoryWord(a.category)} by {a.author} (
                <span className="font-mono">{when(a.created_at)}</span>).
              </p>
            ))}
          </div>
        </Section>
        <Section title="Same case in other runs (same content; this run excluded)">
          {(d.other_runs ?? []).length === 0 ? (
            <Empty title="No other run saw this content" />
          ) : (
            <table className="w-full border-collapse text-left">
              <tbody>
                {(d.other_runs ?? []).map((o) => (
                  <tr key={`${o.suite}/${o.run_id}`} className="border-b border-rule last:border-b-0">
                    <td className="px-3 py-1.5">
                      <RunLink suite={o.suite} runId={o.run_id} config={o.config} caseId={caseId} />
                    </td>
                    <td className="px-2 py-1.5 font-mono text-[12px] text-ink-2">{when(o.started_at)}</td>
                    <td className="px-2 py-1.5">{o.miss ? 'miss' : 'pass'}</td>
                    <td className="px-3 py-1.5 text-right text-ink-2">
                      {Object.entries(o.scores)
                        .map(([k, v]) => `${k} ${metric(v)}`)
                        .join(' · ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>

      <AnnotateDialog
        key={`${caseId}:${d.annotation?.annotation_id ?? 'new'}`}
        open={triaging}
        onOpenChange={setTriaging}
        current={d.annotation}
        target={{ dataset: d.dataset, case_id: d.case_id, case_hash: d.case_hash, run_id: runId }}
      />
    </div>
  )
}

function Missing() {
  return <span className="text-muted">not available</span>
}
