import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { AnnotateDialog, CATEGORIES, type Category, categoryWord } from '@/components/annotate'
import { Outcome } from '@/components/cases'
import { ContentNote, FailingScores, OutputBody, SideBySide, Value } from '@/components/detail'
import { Button, Empty, ErrorPanel, Kbd, KeyHints, Section, Skeleton } from '@/components/ui'
import { useData } from '@/data/context'
import { cn } from '@/lib/cn'
import { useListKeys, useShortcuts } from '@/lib/keys'
import { caseQuery, casesQuery } from '@/data/queries'

export const Route = createFileRoute('/runs/$suite/$runId/triage')({ component: TriagePage })

/**
 * Miss triage (design §10.1, §3 L1): every miss of a run, one by one. J / K move, R / L / E / U
 * open the dialog with that category picked, saving moves on to the next untriaged miss.
 */
function TriagePage() {
  const { suite, runId } = Route.useParams()
  const source = useData()
  const cases = useQuery(casesQuery(source, suite, runId)) // the run page's list, from the cache
  const [untriagedOnly, setUntriagedOnly] = useState(true)
  const [dialog, setDialog] = useState<Category | null>(null)

  // The queue keeps its order while you work: a case triaged a moment ago stays until you leave.
  const [seen, setSeen] = useState<string[]>([])
  const all = useMemo(() => cases.data?.items.filter((r) => r.miss) ?? [], [cases.data])
  const queue = useMemo(
    () => all.filter((r) => !untriagedOnly || !r.annotation || seen.includes(r.case_id)),
    [all, untriagedOnly, seen],
  )
  const { active, setActive, list } = useListKeys<HTMLOListElement>(queue.length)
  const row = active >= 0 ? queue[active] : undefined
  const detail = useQuery({ ...caseQuery(source, suite, runId, row?.case_id ?? ''), enabled: Boolean(row) })
  useShortcuts(
    Object.fromEntries(CATEGORIES.map((c) => [c.key, () => setDialog(c.value)])),
    Boolean(row) && source.mode === 'live',
  )

  if (cases.isPending) return <Skeleton rows={10} />
  if (cases.isError) return <ErrorPanel error={cases.error} />
  const done = all.filter((r) => r.annotation).length

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center gap-3 rounded border border-rule bg-surface px-3 py-2.5">
        <h1 className="text-[16px] font-semibold">Triage misses</h1>
        <Link to="/runs/$suite/$runId" params={{ suite, runId }} className="text-ink-2 hover:underline">
          {suite} / <span className="font-mono text-[12px]">{runId}</span>
        </Link>
        <span className="text-ink-2">
          {done} of {all.length} triaged
        </span>
        <label className="ml-auto inline-flex items-center gap-1.5 text-ink-2">
          <input
            type="checkbox"
            checked={untriagedOnly}
            onChange={(e) => {
              setUntriagedOnly(e.target.checked)
              setSeen([])
            }}
            className="size-3.5 accent-[var(--focus)]"
          />
          Untriaged only
        </label>
      </header>

      {all.length === 0 ? (
        <Section title="Misses">
          <Empty title="This run has no misses">Every case passed and no output errored.</Empty>
        </Section>
      ) : queue.length === 0 ? (
        <Section title="Misses">
          <Empty title="Every miss is triaged">Untick “Untriaged only” to review or correct them.</Empty>
        </Section>
      ) : (
        <div className="grid gap-3 lg:grid-cols-[18rem_minmax(0,1fr)]">
          <Section title={`Queue (${queue.length})`} className="lg:sticky lg:top-14 lg:self-start">
            <ol ref={list} className="max-h-[70vh] overflow-auto">
              {queue.map((r, i) => (
                <li key={r.case_id} data-index={i}>
                  <button
                    type="button"
                    onClick={() => setActive(i)}
                    aria-current={active === i ? 'true' : undefined}
                    className={cn(
                      'flex w-full items-center gap-2 border-b border-rule px-3 py-1.5 text-left hover:bg-hover',
                      active === i && 'bg-select hover:bg-select',
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{r.case_id}</span>
                    <span className="text-[12px] text-ink-2">
                      {r.annotation ? categoryWord(r.annotation.category) : <Outcome row={r} />}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
            <KeyHints hints={[['J K', 'move'], ['R L E U', 'triage']]} />
          </Section>

          <div className="min-w-0 space-y-3">
            {row && (
              <div className="flex flex-wrap items-center gap-2">
                <Link
                  to="/runs/$suite/$runId/cases/$caseId"
                  params={{ suite, runId, caseId: row.case_id }}
                  className="font-mono text-[15px] font-semibold hover:underline"
                >
                  {row.case_id}
                </Link>
                {row.annotation && <span className="text-ink-2">currently {categoryWord(row.annotation.category)}</span>}
                <div className="ml-auto flex flex-wrap gap-1.5">
                  {CATEGORIES.map((c) => (
                    <Button key={c.value} disabled={source.mode !== 'live'} onClick={() => setDialog(c.value)}>
                      {c.label} <Kbd>{c.key.toUpperCase()}</Kbd>
                    </Button>
                  ))}
                </div>
              </div>
            )}
            {detail.isPending ? (
              <Skeleton />
            ) : detail.isError ? (
              <ErrorPanel error={detail.error} />
            ) : (
              <>
                <ContentNote status={detail.data.content.status} />
                <FailingScores scores={detail.data.scores ?? []} output={detail.data.outputs?.[0]} />
                <SideBySide
                  panes={[
                    { title: 'Input', body: <Value value={detail.data.content.case?.input} /> },
                    { title: 'Expected', body: <Value value={detail.data.content.case?.expected} /> },
                    { title: 'Output', body: <OutputBody output={detail.data.outputs?.[0]} /> },
                  ]}
                />
                {dialog && (
                  <AnnotateDialog
                    key={`${row!.case_id}:${dialog}`}
                    open
                    onOpenChange={(open) => !open && setDialog(null)}
                    initial={dialog}
                    current={detail.data.annotation}
                    target={{
                      dataset: detail.data.dataset,
                      case_id: detail.data.case_id,
                      case_hash: detail.data.case_hash,
                      run_id: runId,
                    }}
                    onSaved={() => {
                      setSeen((s) => [...s, detail.data.case_id])
                      setActive((i) => Math.min(i + 1, queue.length - 1))
                    }}
                  />
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
